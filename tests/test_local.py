import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

import llm
import local_setup
from llm import LLMError


def ollama_settings(**kw):
    s = llm.default_settings()
    s["provider"] = "ollama"
    s["providers"]["ollama"].update(model="qwen3.5:4b", **kw)
    return s


class OllamaProviderTest(unittest.TestCase):
    def test_old_v1_url_is_migrated(self):
        path = os.path.join(tempfile.mkdtemp(), "s.json")
        s = llm.default_settings()
        s["providers"]["ollama"]["base_url"] = "http://localhost:11434/v1/"
        llm.save_settings(s, path)
        self.assertEqual(llm.load_settings(path)["providers"]["ollama"]["base_url"], "http://localhost:11434")

    def test_native_chat_request(self):
        calls = []
        def fake_post(url, body, headers, timeout=None):
            calls.append((url, body))
            return {"message": {"content": '{"ok": true}'}}
        with mock.patch("llm._post_json", fake_post):
            out = llm.make_completer(ollama_settings(num_ctx=16384))("sys", "hi")
        self.assertEqual(out, '{"ok": true}')
        url, body = calls[0]
        self.assertEqual(url, "http://localhost:11434/api/chat")
        self.assertEqual((body["format"], body["stream"], body["options"]["num_ctx"], body["think"]), ("json", False, 16384, False))
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})

    def test_retries_without_think_when_unsupported(self):
        bodies = []
        def fake_post(url, body, headers, timeout=None):
            bodies.append(dict(body))
            if "think" in body:
                raise LLMError('HTTP 400: "model does not support thinking"')
            return {"message": {"content": "x"}}
        with mock.patch("llm._post_json", fake_post):
            self.assertEqual(llm.make_completer(ollama_settings())("s", "p"), "x")
        self.assertEqual(["think" in b for b in bodies], [True, False])

    def test_list_models_uses_tags(self):
        with mock.patch("llm._get_json", return_value={"models": [{"name": "b:1"}, {"name": "a:2"}]}) as g:
            self.assertEqual(llm.list_models("ollama", ollama_settings()), ["a:2", "b:1"])
        self.assertEqual(g.call_args[0][0], "http://localhost:11434/api/tags")

    def test_context_budget(self):
        self.assertEqual(llm.context_chars(ollama_settings(num_ctx=8192), "rate"), (8192 - 2500) * 3)
        self.assertEqual(llm.context_chars(ollama_settings(num_ctx=2048), "rate"), 4000)
        s = llm.default_settings()
        s["provider"] = "gemini"
        self.assertEqual(llm.context_chars(s, "rate", remote=123), 123)


class LocalOnlyTest(unittest.TestCase):
    def test_blocks_cloud_providers(self):
        s = ollama_settings()
        s["providers"]["gemini"]["api_key"] = "k"
        s["tasks"] = {"rate": "gemini"}
        self.assertTrue(llm.is_configured(s, "rate"))
        s["local_only"] = True
        self.assertFalse(llm.is_configured(s, "rate"))
        self.assertTrue(llm.is_configured(s, "categorize"))      # Ollama is local
        with self.assertRaises(LLMError):
            llm.make_completer(s, "rate")
        pub = llm.public_settings(s)
        self.assertTrue(pub["providers"]["gemini"]["blocked"])
        self.assertFalse(pub["providers"]["ollama"]["blocked"])


class SetupTest(unittest.TestCase):
    def test_recommendations_by_vram(self):
        best = lambda vram: next(r["model"] for r in local_setup.recommend(vram) if r["best"])
        self.assertEqual(best(4096), "qwen3.5:4b")
        self.assertEqual(best(8192), "qwen3.5:9b")
        self.assertEqual(best(2048), "qwen3.5:2b")   # nothing fits: lightest
        self.assertEqual(best(0), "qwen3.5:2b")      # CPU only
        self.assertFalse(local_setup.recommend(4096)[0]["fits_gpu"])

    def test_pull_rejects_bad_names(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(local_setup.pull_model("x; rm -rf /"))
        self.assertIn("Not a valid model name", out.getvalue())

    def test_status_without_ollama(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")), mock.patch("shutil.which", return_value=None):
            st = local_setup.ollama("http://localhost:11434")
        self.assertEqual((st["ok"], st["installed"], st["detail"]), (False, False, "Not installed."))

    def test_use_model_sets_default(self):
        local_setup.use_model("qwen3.5:4b", ["rate"])
        s = llm.load_settings()
        self.assertEqual((s["provider"], s["providers"]["ollama"]["model"], s["tasks"]["rate"]), ("ollama", "qwen3.5:4b", "ollama"))
        s.update(provider="none", tasks={})
        llm.save_settings(s)

    def test_gpu_nvidia(self):
        with mock.patch("shutil.which", return_value="nvidia-smi"), mock.patch(
                "local_setup._run", return_value="NVIDIA RTX, 8192, 1024"):
            gpu = local_setup.gpu(system="Linux")
        self.assertEqual((gpu["vendor"], gpu["name"], gpu["vram_mb"], gpu["used_mb"], gpu["ollama_gpu"]),
                         ("nvidia", "NVIDIA RTX", 8192, 1024, True))

    def test_gpu_amd_linux_sysfs(self):
        with tempfile.TemporaryDirectory() as drm:
            device = os.path.join(drm, "card0", "device")
            os.makedirs(device)
            for filename, value in {"vendor": "0x1002", "mem_info_vram_total": str(8 * 1024 ** 3),
                                    "mem_info_vram_used": str(2 * 1024 ** 3), "product_name": "Radeon RX"}.items():
                with open(os.path.join(device, filename), "w") as f:
                    f.write(value)
            with mock.patch("shutil.which", return_value=None):
                gpu = local_setup.gpu(system="Linux", drm=drm)
        self.assertEqual((gpu["vendor"], gpu["name"], gpu["vram_mb"], gpu["used_mb"]),
                         ("amd", "Radeon RX", 8192, 2048))

    def test_gpu_prefers_amd_over_intel_on_linux(self):
        with tempfile.TemporaryDirectory() as drm:
            for card, vendor in (("card0", "0x8086"), ("card1", "0x1002")):
                device = os.path.join(drm, card, "device")
                os.makedirs(device)
                with open(os.path.join(device, "vendor"), "w") as f:
                    f.write(vendor)
                if vendor == "0x1002":
                    with open(os.path.join(device, "mem_info_vram_total"), "w") as f:
                        f.write(str(4 * 1024 ** 3))
            with mock.patch("shutil.which", return_value=None):
                gpu = local_setup.gpu(system="Linux", drm=drm)
        self.assertEqual(gpu["vendor"], "amd")

    def test_gpu_intel_linux(self):
        with tempfile.TemporaryDirectory() as drm:
            device = os.path.join(drm, "card0", "device")
            os.makedirs(device)
            with open(os.path.join(device, "vendor"), "w") as f:
                f.write("0x8086")
            with mock.patch("shutil.which", return_value=None):
                gpu = local_setup.gpu(system="Linux", drm=drm)
        self.assertEqual((gpu["vendor"], gpu["vram_mb"], gpu["ollama_gpu"]), ("intel", 0, False))
        self.assertIn("CPU", gpu["detail"])

    def test_gpu_linux_intel_only_card(self):
        with tempfile.TemporaryDirectory() as drm:
            device = os.path.join(drm, "card0", "device")
            os.makedirs(device)
            with open(os.path.join(device, "vendor"), "w") as f:
                f.write("0x8086")
            with mock.patch("shutil.which", return_value=None):
                gpu = local_setup.gpu(system="Linux", drm=drm)
        self.assertEqual(gpu["vendor"], "intel")
        self.assertFalse(gpu["ollama_gpu"])

    def test_gpu_windows_amd_dict(self):
        with mock.patch("shutil.which", return_value=None), mock.patch(
                "local_setup._run", return_value='{"Name":"AMD Radeon 780M","AdapterRAM":4294967295}') as run:
            gpu = local_setup.gpu(system="Windows")
        self.assertEqual((gpu["vendor"], gpu["vram_mb"], gpu["ollama_gpu"]), ("amd", 4095, True))
        self.assertIn("at least", gpu["detail"])
        self.assertEqual(run.call_args.kwargs["timeout"], 10)

    def test_gpu_windows_prefers_amd_in_list(self):
        output = '[{"Name":"Intel UHD","AdapterRAM":1024},{"Name":"AMD Radeon","AdapterRAM":8589934592}]'
        with mock.patch("shutil.which", return_value=None), mock.patch("local_setup._run", return_value=output):
            gpu = local_setup.gpu(system="Windows")
        self.assertEqual(gpu["vendor"], "amd")

    def test_gpu_apple_silicon(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("platform.machine", return_value="arm64"), \
                mock.patch("local_setup._run", side_effect=["Apple M2", str(16 * 1024 ** 3)]):
            gpu = local_setup.gpu(system="Darwin")
        self.assertEqual((gpu["vendor"], gpu["name"], gpu["vram_mb"]), ("apple", "Apple M2", 10649))
        self.assertIn("10.4 GB", gpu["detail"])

    def test_gpu_nothing_found_and_bad_outputs_do_not_raise(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("local_setup.glob.glob", return_value=[]):
            gpu = local_setup.gpu(system="FreeBSD")
        self.assertEqual((gpu["ok"], gpu["vendor"], gpu["ollama_gpu"]), (False, None, False))
        with mock.patch("shutil.which", return_value="nvidia-smi"), mock.patch("local_setup._run", return_value="bad"), \
                mock.patch("local_setup.glob.glob", return_value=[]):
            self.assertFalse(local_setup.gpu(system="Linux")["ok"])

    def test_status_keeps_cpu_recommendations_for_intel(self):
        intel = {"ok": True, "vendor": "intel", "ollama_gpu": False, "vram_mb": 24000}
        with mock.patch("local_setup.gpu", return_value=intel), \
                mock.patch("local_setup.ollama", return_value={"models": []}), \
                mock.patch("local_setup.memory", return_value={"ok": False}), \
                mock.patch("local_setup.ffmpeg", return_value={"ok": False}), \
                mock.patch("local_setup.whisper", return_value={"ok": False}), \
                mock.patch("local_setup.embed_status", return_value={}):
            status = local_setup.status()
        self.assertEqual(next(r["model"] for r in status["recommended"] if r["best"]), "qwen3.5:2b")


class EmbedSetupTest(unittest.TestCase):
    def test_embed_status_and_choice(self):
        import storage
        e = local_setup.embed_status(llm.default_settings(), {"models": [{"name": "qwen3-embedding:0.6b"}]})
        self.assertEqual((e["model"], e["installed"]), ("qwen3-embedding:0.6b", True))
        self.assertEqual([c["installed"] for c in e["choices"]], [True, False])
        self.assertIn("pieces", e)
        self.assertFalse(local_setup.embed_status(llm.default_settings(), {})["installed"])   # Ollama not running
        before = llm.load_settings().get("embed_model")
        try:
            local_setup.use_embed_model("qwen3-embedding:4b")
            self.assertEqual(llm.load_settings()["embed_model"], "qwen3-embedding:4b")
            with self.assertRaises(ValueError):
                local_setup.use_embed_model("bad name; rm")
        finally:
            s = llm.load_settings()
            s["embed_model"] = before
            llm.save_settings(s)
        storage.save_embeddings("m-clear", [("h1", b"\0" * 4, 1), ("h2", b"\0" * 4, 1)])
        self.assertEqual(storage.clear_embeddings("m-clear"), 2)
        self.assertEqual(storage.clear_embeddings("m-clear"), 0)

    def test_pull_of_the_embedding_model_builds_the_index(self):
        import dashboard
        import embedder
        with mock.patch("local_setup.pull_model", return_value=True), mock.patch("embedder.run_embed") as run:
            self.assertTrue(dashboard._pull_then_index(embedder.model_name(llm.load_settings())))
            self.assertTrue(dashboard._pull_then_index("qwen3.5:4b"))
        run.assert_called_once()
