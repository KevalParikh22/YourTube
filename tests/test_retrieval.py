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
