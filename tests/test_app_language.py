import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(ROOT, "web", "static", "app.js")

# app.js is a plain script with no build step. Load it in a VM with tiny DOM stubs so the
# renderer functions and the click handler can be called without a browser.
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8').replace(/applyTheme\(\);\s*load\(\);\s*$/, '');
const handlers = {};
const listen = (type, fn) => { (handlers[type] ||= []).push(fn); };
const sandbox = {
  console, setTimeout, clearTimeout, URLSearchParams,
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  localStorage: { getItem: () => null, setItem: () => {} },
  location: { hash: '', replace() {} },
  addEventListener: listen,
  document: { addEventListener: listen, querySelector: () => ({ addEventListener() {}, focus() {} }),
              querySelectorAll: () => [] },
  fetch: () => Promise.reject(new Error('unused')),
};
const ctx = vm.createContext(sandbox);
vm.runInContext(src + `
globalThis.__t = { S, otherLang, drawerSummary, drawerRating, viewAbout,
  setSpies(sum, rate) { startSummary = sum; startRate = rate; } };
`, ctx);
ctx.__handlers = handlers;
const t = ctx.__t;
t.S.data = {
  ai: { language: 'English', languages: ['English', 'French'], ready_for: { rate: true, summarize: true } },
  libraries: [{ id: 1, name: 'Main' }], voice: false,
};
t.S.summaryCache = {};
function summaryHtml(language) {
  t.S.summaryCache['abc'] = { summary: { language, tldr: 'A point', model: 'test' }, moments: {} };
  return t.drawerSummary({ video_id: 'abc' });
}
function ratingHtml(rating) {
  return t.drawerRating({ video_id: 'abc', rating });
}
function settingsHtml(other) {
  t.S.data.other_language = other;
  return t.viewAbout();
}
async function clickRedo(other) {
  t.S.data.other_language = other;
  const calls = [];
  t.setSpies(async (ids, note) => calls.push(['summary', ids, note]),
             async (ids, note) => calls.push(['rate', ids, note]));
  const el = { dataset: { act: 'redo-language' }, tagName: 'BUTTON', getAttribute: () => null };
  await ctx.__handlers.click[0]({ target: { closest: () => el } });
  return calls;
}
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AppLanguageTests(unittest.TestCase):
    def run_js(self, script):
        result = subprocess.run(["node", "-e", HARNESS + "\n" + script, APP_JS],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_other_lang_helper(self):
        out = self.run_js(r"""
console.log(JSON.stringify([
  t.otherLang(null), t.otherLang(''), t.otherLang('  English '), t.otherLang('english'),
  t.otherLang('French'), t.otherLang('  french '),
]));
""")
        self.assertEqual(out, [False, False, False, False, True, True])

    def test_summary_note_only_for_another_language(self):
        out = self.run_js(r"""
console.log(JSON.stringify({ other: summaryHtml('French'), same: summaryHtml(' English '),
                             empty: summaryHtml(''), none: summaryHtml(null) }));
""")
        self.assertIn('Summary in French', out["other"])
        self.assertIn('data-act="summarize-one" data-id="abc"', out["other"])
        self.assertIn('Redo in English', out["other"])
        for key in ("same", "empty", "none"):
            self.assertNotIn('Summary in', out[key])

    def test_rating_note_only_for_another_language(self):
        out = self.run_js(r"""
const base = { score: 7, reason: 'Useful', verdict: 'watch', breakdown: {}, skim: [], points: [], model: 'test' };
console.log(JSON.stringify({ other: ratingHtml({ ...base, language: 'French' }),
                             same: ratingHtml({ ...base, language: 'english' }),
                             empty: ratingHtml({ ...base, language: '' }),
                             none: ratingHtml(base), unrated: ratingHtml(null) }));
""")
        self.assertIn('Rating in French', out["other"])
        self.assertIn('data-act="rate-one" data-id="abc"', out["other"])
        self.assertIn('Redo in English', out["other"])
        for key in ("same", "empty", "none", "unrated"):
            self.assertNotIn('Rating in', out[key])

    def test_settings_note_counts_and_button(self):
        out = self.run_js(r"""
console.log(JSON.stringify({ absent: settingsHtml(undefined),
  empty: settingsHtml({ summaries: [], ratings: [] }),
  both: settingsHtml({ summaries: ['a', 'b'], ratings: ['c', 'd', 'e'] }),
  summaries: settingsHtml({ summaries: ['a'], ratings: [] }),
  ratings: settingsHtml({ summaries: [], ratings: ['c', 'd'] }) }));
""")
        for key in ("absent", "empty"):
            self.assertNotIn('data-act="redo-language"', out[key])
            self.assertNotIn('are in another language', out[key])
        self.assertIn('2 summaries and 3 ratings are in another language.', out["both"])
        self.assertIn('data-act="redo-language"', out["both"])
        self.assertIn('Redo in English', out["both"])
        self.assertIn('1 summary are in another language.', out["summaries"])
        self.assertNotIn('rating', out["summaries"].split('id="ai-lang"')[1].split('</section>')[0])
        self.assertIn('2 ratings are in another language.', out["ratings"])
        self.assertNotIn('summary', out["ratings"].split('id="ai-lang"')[1].split('</section>')[0])

    def test_redo_language_calls_bulk_actions(self):
        out = self.run_js(r"""
(async () => console.log(JSON.stringify({
  both: await clickRedo({ summaries: ['a', 'b'], ratings: ['c'] }),
  summaries: await clickRedo({ summaries: ['a'], ratings: [] }),
  ratings: await clickRedo({ summaries: [], ratings: ['c'] }),
  none: await clickRedo(undefined),
})))();
""")
        self.assertEqual(out["both"], [
            ["summary", ["a", "b"], "in the current language"],
            ["rate", ["c"], "in the current language"],
        ])
        self.assertEqual(out["summaries"], [["summary", ["a"], "in the current language"]])
        self.assertEqual(out["ratings"], [["rate", ["c"], "in the current language"]])
        self.assertEqual(out["none"], [])


if __name__ == "__main__":
    unittest.main()
