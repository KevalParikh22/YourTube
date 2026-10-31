import contextlib
import io
import sqlite3
import unittest

import embedder
import retrieval
import storage
from tests.helpers import playlist, temp_db, video

# A fake "meaning" model: each group of words is one dimension, so synonyms land close together
GROUPS = [("lora", "adapter", "adapters", "low-rank", "cheaply", "few", "weights", "parameters"),
          ("docker", "container", "neo4j", "database", "locally"),
          ("pasta", "recipe", "kitchen", "boil")]


def fake_vec(text):
    words = text.lower().replace("?", " ").replace(".", " ").split()
    return [sum(w in g for w in words) + 0.01 for g in GROUPS]


def fake_embed(texts):
    return [fake_vec(t) for t in texts]


CAT = "RetrievalTestCat"


class RetrievalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        storage.save_playlist(playlist("PLR2"), [video("r_ft", "Fine tuning talk", category=CAT),
                                                 video("r_db", "Graph project", category=CAT),
                                                 video("r_ck", "Cooking show", category=CAT)])
        storage.save_details("r_ft", {"status": "done", "segments": [
            {"start": 0, "text": "welcome to the channel"},
            {"start": 254, "text": "we use LoRA adapters so only a few weights are trained"}]})
        storage.save_details("r_db", {"status": "done", "segments": [{"start": 3662, "text": "run neo4j in a docker container"}]})
        storage.save_details("r_ck", {"status": "done", "segments": [{"start": 5, "text": "boil water for the pasta recipe"}]})
        with contextlib.redirect_stdout(io.StringIO()):
            embedder.run_embed(embed_fn=fake_embed)

    F = {"categories": [CAT]}

    def ask(self, q, **kw):
        return retrieval.retrieve(q, k=kw.pop("k", 3), filters={**self.F, **kw}, embed_query=fake_vec)

    def test_meaning_finds_paraphrase(self):
        r = self.ask("How do I adapt cheaply by training few parameters?")
        self.assertEqual(r["mode"], "hybrid")
        top = r["pieces"][0]
        self.assertEqual((top["video_id"], top["kind"], top["t"]), ("r_ft", "transcript", 254))
        self.assertEqual(top["title"], "Fine tuning talk")
        self.assertEqual(self.ask("Running the database locally")["pieces"][0]["video_id"], "r_db")

    def test_filters(self):
        self.assertTrue(all(p["video_id"] == "r_ck" for p in self.ask("pasta", video_ids=["r_ck"])["pieces"]))
        storage.update_videos(["r_ck"], status="completed")
        try:
            self.assertNotIn("r_ck", [p["video_id"] for p in self.ask("boil the pasta", include_done=False, k=10)["pieces"]])
        finally:
            storage.update_videos(["r_ck"], status="unwatched")
        self.assertEqual(retrieval.retrieve("pasta", filters={"categories": ["NoSuchCategory"]}, embed_query=fake_vec)["pieces"], [])

    def test_keyword_fallback(self):
        r = retrieval.retrieve("neo4j docker", k=3, filters=self.F, model="not-embedded-model")
        self.assertEqual(r["mode"], "keyword")
        self.assertEqual(r["pieces"][0]["video_id"], "r_db")

        def broken(text):
            raise RuntimeError("ollama down")
        self.assertEqual(retrieval.retrieve("neo4j docker", filters=self.F, embed_query=broken)["mode"], "keyword")

    def test_incremental_and_prune(self):
        model = embedder.model_name({})
        self.assertEqual(storage.docs_missing_embeddings(model), [])
        storage.save_playlist(playlist("PLR2"), [video("r_ft", "Fine tuning talk", category=CAT), video("r_db", "Graph project", category=CAT),
                                                 video("r_ck", "Cooking show", category=CAT)])
        self.assertEqual(storage.docs_missing_embeddings(model), [])      # re-saved, unchanged titles keep their vectors
        storage.update_videos(["r_ck"], notes="my pasta notes")
        self.assertEqual([d["text"] for d in storage.docs_missing_embeddings(model)], ["my pasta notes"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            embedder.run_embed(embed_fn=fake_embed)
        self.assertIn("1 new or changed", out.getvalue())
        storage.update_videos(["r_ck"], notes="")
        self.assertGreaterEqual(storage.prune_embeddings(model), 1)      # the notes vector is orphaned now

    def test_kinds_filter_and_meaning_search(self):
        r = self.ask("lora adapters weights", k=10, kinds=["title"])
        self.assertTrue(r["pieces"] and all(p["kind"] == "title" for p in r["pieces"]))
        res = retrieval.search_by_meaning("cheaply adapt with few parameters", embed_query=fake_vec)
        self.assertEqual(res["mode"], "hybrid")
        top = res["results"][0]
        self.assertEqual(top["video_id"], "r_ft")
        self.assertIn("\x02few\x03", " ".join(h["snippet"] for h in top["hits"]))   # the question's words are marked
        self.assertEqual(retrieval.search_by_meaning("ab")["short"], True)

    def test_per_video_cap(self):
        many = [{"start": i * 40, "text": f"lora adapters part {i}"} for i in range(8)]
        storage.save_details("r_ft", {"status": "done", "segments": many})
        with contextlib.redirect_stdout(io.StringIO()):
            embedder.run_embed(embed_fn=fake_embed)
        r = self.ask("lora adapters", k=8)
        self.assertEqual(sum(p["video_id"] == "r_ft" for p in r["pieces"][:retrieval.KEEP_TOP]), retrieval.KEEP_TOP)
        self.assertEqual([p["video_id"] for p in r["pieces"][retrieval.KEEP_TOP:]].count("r_ft"), 0)   # capped after the top
        self.assertGreater(sum(p["video_id"] == "r_ft" for p in self.ask("lora adapters", k=8, video_ids=["r_ft"])["pieces"]), 4)


class HashMigrationTest(unittest.TestCase):
    def test_old_index_gets_hashes(self):
        db = temp_db()
        storage.save_playlist(playlist(), [video("a", "Title A")], db_path=db)
        conn = sqlite3.connect(db)
        conn.executescript("DROP INDEX idx_search_docs_hash; ALTER TABLE search_docs DROP COLUMN hash;")
        conn.close()
        storage.init_db(db)
        self.assertEqual(storage.embedding_status("m", db_path=db), {"pieces": 1, "embedded": 0})
        self.assertEqual(len(storage.docs_missing_embeddings("m", db_path=db)), 1)
        self.assertEqual(retrieval.keyword_query('How do I run "neo4j" in a container?'), '"run" OR "neo4j" OR "container"')


class CloudEmbeddingTest(unittest.TestCase):
    def test_local_embedder_cache_key(self):
        import llm
        s = llm.default_settings()
        emb = embedder.embedder_for(s)
        self.assertEqual(emb.model, "qwen3-embedding:0.6b")
        self.assertEqual(emb.provider, "local")
        s["embed_model"] = "custom-local:4b"
        self.assertEqual(embedder.embedder_for(s).model, "custom-local:4b")

    def test_gemini_request_shape(self):
        import json
        import llm
        from unittest import mock
        s = llm.default_settings()
        s["tasks"] = {"embed": "gemini"}
        s["providers"]["gemini"]["api_key"] = "test-gemini-key"
        emb = embedder.embedder_for(s)
        self.assertEqual(emb.model, "gemini:gemini-embedding-001")
        self.assertEqual(emb.provider, "gemini")

        captured = []
        def fake_urlopen(req, timeout=None):
            captured.append(req)
            body = json.loads(req.data.decode("utf-8"))
            n = len(body.get("requests", []))
            resp_data = {"embeddings": [{"values": [3.0, 4.0]} for _ in range(n)]}
            m = mock.MagicMock()
            m.__enter__.return_value = io.BytesIO(json.dumps(resp_data).encode("utf-8"))
            return m

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            vecs = emb.docs(["doc1", "doc2"])
            self.assertEqual(len(captured), 1)
            req = captured[0]
            self.assertEqual(req.full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents")
            self.assertEqual(req.headers.get("X-goog-api-key"), "test-gemini-key")
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(len(body["requests"]), 2)
            self.assertEqual(body["requests"][0]["taskType"], "RETRIEVAL_DOCUMENT")
            self.assertEqual(body["requests"][0]["model"], "models/gemini-embedding-001")
            self.assertEqual(body["requests"][0]["content"]["parts"][0]["text"], "doc1")
            self.assertAlmostEqual(vecs[0][0], 0.6, places=4)
            self.assertAlmostEqual(vecs[0][1], 0.8, places=4)

            # query task type
            captured.clear()
            qvecs = emb.queries(["query1"])
            self.assertEqual(len(captured), 1)
            qbody = json.loads(captured[0].data.decode("utf-8"))
            self.assertEqual(qbody["requests"][0]["taskType"], "RETRIEVAL_QUERY")
            self.assertAlmostEqual(qvecs[0][0], 0.6, places=4)

    def test_openai_request_shape(self):
        import json
        import llm
        from unittest import mock
        s = llm.default_settings()
        s["tasks"] = {"embed": "openai"}
        s["providers"]["openai"]["api_key"] = "test-openai-key"
        emb = embedder.embedder_for(s)
        self.assertEqual(emb.model, "openai:text-embedding-3-small")
        self.assertEqual(emb.provider, "openai")

        captured = []
        def fake_urlopen(req, timeout=None):
            captured.append(req)
            body = json.loads(req.data.decode("utf-8"))
            inputs = body.get("input", [])
            resp_data = {"object": "list", "data": [{"object": "embedding", "index": i, "embedding": [0.0, 5.0]} for i in range(len(inputs))]}
            m = mock.MagicMock()
            m.__enter__.return_value = io.BytesIO(json.dumps(resp_data).encode("utf-8"))
            return m

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            vecs = emb.docs(["doc A", "doc B"])
            self.assertEqual(len(captured), 1)
            req = captured[0]
            self.assertEqual(req.full_url, "https://api.openai.com/v1/embeddings")
            self.assertEqual(req.headers.get("Authorization"), "Bearer test-openai-key")
            body = json.loads(req.data.decode("utf-8"))
            self.assertEqual(body["model"], "text-embedding-3-small")
            self.assertEqual(body["input"], ["doc A", "doc B"])
            self.assertAlmostEqual(vecs[0][0], 0.0, places=4)
            self.assertAlmostEqual(vecs[0][1], 1.0, places=4)

            captured.clear()
            qvecs = emb.queries(["query A"])
            self.assertEqual(len(captured), 1)
            qbody = json.loads(captured[0].data.decode("utf-8"))
            self.assertEqual(qbody["input"], ["query A"])
            self.assertAlmostEqual(qvecs[0][1], 1.0, places=4)

    def test_local_only_blocks_cloud_embedding(self):
        import llm
        s = llm.default_settings()
        s["tasks"] = {"embed": "gemini"}
        s["providers"]["gemini"]["api_key"] = "test-gemini-key"
        s["local_only"] = True
        emb = embedder.embedder_for(s)
        self.assertEqual(emb.provider, "local")
        self.assertEqual(emb.model, embedder.DEFAULT_MODEL)

    def test_provider_switch_caches_separately_and_switching_back_needs_no_work(self):
        import llm
        s_local = llm.default_settings()
        s_local["embed_model"] = "test_custom_local_model"

        s_gemini = llm.default_settings()
        s_gemini["tasks"] = {"embed": "gemini"}
        s_gemini["providers"]["gemini"]["api_key"] = "gem-key"
        s_gemini["providers"]["gemini"]["embed_model"] = "test_gemini_model"

        emb_l = embedder.embedder_for(s_local)
        emb_g = embedder.embedder_for(s_gemini)
        self.assertEqual(emb_l.model, "test_custom_local_model")
        self.assertEqual(emb_g.model, "gemini:test_gemini_model")

        try:
            # 1. Embed with local
            fake_local_vecs = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
            embedder.run_embed(embed_fn=fake_local_vecs, settings=s_local)
            st_l = storage.embedding_status(emb_l.model)
            self.assertGreater(st_l["embedded"], 0)
            st_g = storage.embedding_status(emb_g.model)
            self.assertEqual(st_g["embedded"], 0)

            # 2. Switch to gemini and build its cache
            fake_gemini_vecs = lambda texts: [[0.0, 1.0, 0.0] for _ in texts]
            embedder.run_embed(embed_fn=fake_gemini_vecs, settings=s_gemini)
            st_g2 = storage.embedding_status(emb_g.model)
            self.assertEqual(st_g2["embedded"], st_l["embedded"])
            # local cache was preserved!
            st_l2 = storage.embedding_status(emb_l.model)
            self.assertEqual(st_l2["embedded"], st_l["embedded"])

            # 3. Switch back to local: no work needed!
            called = []
            def counting_embed(texts):
                called.append(texts)
                return [[1.0, 0.0, 0.0] for _ in texts]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                embedder.run_embed(embed_fn=counting_embed, settings=s_local)
            self.assertEqual(len(called), 0)
            self.assertIn("up to date", out.getvalue())
        finally:
            storage.clear_embeddings(emb_l.model)
            storage.clear_embeddings(emb_g.model)

    def test_retrieval_uses_same_embedder_as_index(self):
        import llm
        import numpy as np
        from unittest import mock
        storage.save_playlist(playlist("PL_RET"), [video("v_ret", "Video Ret")])
        s_gemini = llm.default_settings()
        s_gemini["tasks"] = {"embed": "gemini"}
        s_gemini["providers"]["gemini"]["api_key"] = "gem-key"

        emb = embedder.embedder_for(s_gemini)
        self.assertEqual(emb.model, "gemini:gemini-embedding-001")

        mock_emb = mock.MagicMock()
        mock_emb.model = "gemini:gemini-embedding-001"
        mock_emb.queries.return_value = [[0.0, 1.0, 0.0]]

        mock_rows = [{"id": 1, "video_id": "v_ret", "kind": "transcript"}]
        mock_mat = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)

        with mock.patch("embedder.embedder_for", return_value=mock_emb), \
             mock.patch("retrieval._matrix", return_value=(mock_rows, mock_mat)):
            r = retrieval.retrieve("find pasta", settings=s_gemini)
            mock_emb.queries.assert_called_once_with(["find pasta"])
            self.assertEqual(r["mode"], "hybrid")

    def test_local_setup_embed_status_names_provider(self):
        import local_setup
        import llm
        s_loc = llm.default_settings()
        st_loc = local_setup.embed_status(s_loc, {})
        self.assertEqual(st_loc["provider"], "local")

        s_gem = llm.default_settings()
        s_gem["tasks"] = {"embed": "gemini"}
        s_gem["providers"]["gemini"]["api_key"] = "gem-key"
        st_gem = local_setup.embed_status(s_gem, {})
        self.assertEqual(st_gem["provider"], "gemini")
        self.assertEqual(st_gem["model"], "gemini:gemini-embedding-001")
        self.assertTrue(st_gem["installed"])
