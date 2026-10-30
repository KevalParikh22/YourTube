import json
import os
import subprocess
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DockerSetupTests(unittest.TestCase):
    def inspect_paths(self, env):
        code = """
import json, storage, llm, classifier, applog, local_asr, local_visual, voice, dashboard
print(json.dumps([storage.DB_PATH, llm.SETTINGS_PATH, classifier.TAXONOMY_PATH,
 applog.LOG_DIR, local_asr.CACHE, local_visual.CACHE, local_visual.FRAMES,
 voice.VOICE_DIR, dashboard.FRAMES_DIR]))
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def clean_env(self):
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ROOT}
        env.update({k: v for k, v in os.environ.items() if k.startswith("SYSTEMROOT")})
        return env

    def test_data_dir_paths_and_overrides(self):
        with tempfile.TemporaryDirectory() as data:
            env = self.clean_env()
            env["YTKB_DATA_DIR"] = data
            paths = self.inspect_paths(env)
            self.assertEqual(paths, [os.path.join(data, "yt_kb.db"), os.path.join(data, "settings.json"),
                os.path.join(data, "taxonomy.json"), os.path.join(data, "logs"),
                os.path.join(data, "media_cache", "asr"), os.path.join(data, "media_cache", "visual"),
                os.path.join(data, "frames"), os.path.join(data, "media_cache", "voice"), os.path.join(data, "frames")])
            env["YTKB_DB"] = os.path.join(data, "override.db")
            self.assertEqual(self.inspect_paths(env)[0], env["YTKB_DB"])

    def test_unset_data_dir_keeps_repo_paths(self):
        paths = self.inspect_paths(self.clean_env())
        self.assertEqual(paths[0], os.path.join(ROOT, "yt_kb.db"))
        self.assertEqual(paths[1], os.path.join(ROOT, "settings.json"))
        self.assertEqual(paths[2], os.path.join(ROOT, "taxonomy.json"))

    def test_ollama_environment_default_and_custom(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = os.path.join(tmp, "settings.json")
            env = self.clean_env()
            env.update(YTKB_SETTINGS=settings, YTKB_OLLAMA_URL="http://host.docker.internal:11434")
            for address, expected in (("http://localhost:11434", "http://host.docker.internal:11434"),
                                      ("http://custom:11434", "http://custom:11434")):
                with open(settings, "w") as f:
                    json.dump({"providers": {"ollama": {"base_url": address}}}, f)
                result = subprocess.run([sys.executable, "-c", "import llm; print(llm.load_settings()['providers']['ollama']['base_url'])"],
                    cwd=ROOT, env=env, check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout.strip(), expected)

    def test_host_option_binds_and_warns(self):
        import contextlib
        import io
        from unittest import mock
        import dashboard
        self.addCleanup(setattr, dashboard, "TOKEN", dashboard.TOKEN)
        for argv, address, warns in ((["--no-open"], "127.0.0.1", False),
                                     (["--no-open", "--host", "0.0.0.0"], "0.0.0.0", True)):
            fake_server = mock.MagicMock()
            fake_server.serve_forever.side_effect = KeyboardInterrupt
            buf = io.StringIO()
            with mock.patch("dashboard.ThreadingHTTPServer", return_value=fake_server) as server, \
                 contextlib.redirect_stdout(buf):
                dashboard.main(argv)
            self.assertEqual(server.call_args[0][0], (address, 8765))
            self.assertEqual("Warning: listening on 0.0.0.0" in buf.getvalue(), warns)

if __name__ == "__main__":
    unittest.main()
