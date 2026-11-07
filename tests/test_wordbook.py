"""wordbook 的单元测试。

全部离线：不查词典、不起网络，只测纯逻辑和 Windows 上的绑定行为。
运行： python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import pathlib
import socket
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wordbook  # noqa: E402

PAYLOAD = {
    "word": "serendipity",
    "uk": "ˌserənˈdɪpəti",
    "us": "ˌserənˈdɪpəti",
    "phonetic": "",
    "entries": [
        {"pos": "n.", "zh": "意外发现美好事物的运气，机缘巧合", "en": "good luck in making fortunate discoveries"},
    ],
    "examples": [
        {"en": "Some of the best effects have been the result of serendipity.",
         "zh": "我花园里最珍贵的几件物品是机缘巧合之下意外所得。"},
    ],
    "forms": ["复数 serendipities"],
    "exam": ["GRE", "SAT"],
    "web": ["意外发现"],
    "source": "youdao",
}

MULTI_POS = {
    "word": "ephemeral",
    "entries": [
        {"pos": "adj.", "zh": "短暂的", "en": "lasting a very short time"},
        {"pos": "n.", "zh": "短生植物", "en": "anything short-lived"},
    ],
    "examples": [],
    "forms": [],
    "exam": [],
    "web": [],
}


def cfg(**fields) -> dict:
    base = {k: False for k in wordbook.DEFAULT_FIELDS}
    base.update({"pos": True, "zh": True})
    base.update(fields)
    return {"fields": base, "template": ""}


class TestInput(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(wordbook.normalize_word("  Run  "), "run")
        self.assertEqual(wordbook.normalize_word("Take   Into  Account"), "take into account")

    def test_word_re_accepts_phrases(self):
        self.assertTrue(wordbook.WORD_RE.match("run"))
        self.assertTrue(wordbook.WORD_RE.match("take into account"))
        self.assertTrue(wordbook.WORD_RE.match("well-known"))

    def test_word_re_rejects_injection(self):
        self.assertFalse(wordbook.WORD_RE.match("<script>"))
        self.assertFalse(wordbook.WORD_RE.match("a" * 60))
        self.assertFalse(wordbook.WORD_RE.match("123"))


class TestFormat(unittest.TestCase):
    def test_default_is_two_plain_lines(self):
        out = wordbook.format_entry(PAYLOAD, cfg())
        lines = out.rstrip("\n").splitlines()
        self.assertEqual(lines, ["serendipity", "n. 意外发现美好事物的运气，机缘巧合"])
        self.assertNotIn("##", out)
        self.assertNotIn("**", out)

    def test_pos_off(self):
        out = wordbook.format_entry(PAYLOAD, cfg(pos=False))
        self.assertEqual(out.splitlines()[1], "意外发现美好事物的运气，机缘巧合")

    def test_zh_off_drops_chinese_line(self):
        out = wordbook.format_entry(PAYLOAD, cfg(zh=False, en=True))
        self.assertEqual(out.splitlines()[0], "serendipity")
        self.assertIn("英文：", out)
        self.assertNotIn("意外发现", out)

    def test_extra_fields_are_plain_labels(self):
        out = wordbook.format_entry(
            PAYLOAD, cfg(phonetic=True, examples=True, forms=True, exam=True, web=True, time=True)
        )
        for marker in ("音标：", "例句：", "变形：", "考试：", "网络释义：", "收录："):
            self.assertIn(marker, out)
        self.assertNotIn("- **", out)

    def test_multi_pos_keeps_pairing(self):
        line = wordbook.format_entry(MULTI_POS, cfg()).splitlines()[1]
        self.assertEqual(line, "adj. 短暂的；n. 短生植物")

    def test_template_with_spaces(self):
        out = wordbook.format_entry(PAYLOAD, {"fields": {}, "template": "{{ word }}\t{{ zh }}"})
        self.assertTrue(out.startswith("serendipity"))
        self.assertNotIn("{{", out)

    def test_template_without_word_line_gets_one(self):
        out = wordbook.format_entry(PAYLOAD, {"fields": {}, "template": "{{zh}}"})
        self.assertEqual(out.splitlines()[0], "serendipity", "模板丢了单词行，判重会认不出来")

    def test_template_keeping_word_first_has_no_duplicate(self):
        out = wordbook.format_entry(PAYLOAD, {"fields": {}, "template": "{{word}}\n{{zh}}"})
        self.assertEqual(out.splitlines()[0], "serendipity")
        self.assertEqual(out.splitlines()[1], "意外发现美好事物的运气，机缘巧合")


class TestParse(unittest.TestCase):
    def test_parse_entries(self):
        text = "run\nv. 跑\n\nserendipity\nn. 意外发现\n\n"
        self.assertEqual(wordbook.parse_entries(text),
                         [("run", "v. 跑"), ("serendipity", "n. 意外发现")])

    def test_parse_skips_blank_and_extra_lines(self):
        text = "run\nv. 跑\n音标：/rʌn/\n\n\nlucid\nadj. 明晰的\n"
        entries = wordbook.parse_entries(text)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0][0], "run")

    def test_is_saved_uses_first_line(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("Run\nv. 跑\n")
            self.assertTrue(wordbook.is_saved("run", path))
            self.assertFalse(wordbook.is_saved("lucid", path))


class TestCsv(unittest.TestCase):
    def test_formula_injection_is_escaped(self):
        self.assertEqual(wordbook._csv_safe("=1+1"), "'=1+1")
        self.assertEqual(wordbook._csv_safe("+1"), "'+1")
        self.assertEqual(wordbook._csv_safe("@x"), "'@x")
        self.assertEqual(wordbook._csv_safe("普通"), "普通")

    def test_export_takes_second_line_only(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("serendipity\nn. 意外发现\n音标：/x/\n  例句一行\n\n")
            old = wordbook.NOTES_PATH
            wordbook.NOTES_PATH = path
            try:
                text = wordbook.export_anki_csv().decode("utf-8-sig")
            finally:
                wordbook.NOTES_PATH = old
            self.assertIn("serendipity,n. 意外发现", text)
            self.assertNotIn("例句一行", text)


class TestUndo(unittest.TestCase):
    def _seed(self, directory: str):
        path = os.path.join(directory, "notes.txt")
        block = "lucid\nadj. 明晰的\n\n"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(block)
        wordbook._UNDO.clear()
        wordbook._UNDO.update({
            "path": path, "size": 0, "word": "lucid", "block": block,
            "after": os.path.getsize(path),
        })
        return path

    def test_undo_removes_last_entry(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._seed(d)
            result = wordbook.undo_last()
            self.assertTrue(result["ok"])
            self.assertFalse(os.path.exists(path), "撤到空文件时应该收掉")

    def test_undo_refuses_after_manual_edit(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._seed(d)
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write("manual\n手写的\n\n")
            result = wordbook.undo_last()
            self.assertFalse(result["ok"])
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("manual", body)
            self.assertIn("lucid", body)

    def test_undo_without_state(self):
        wordbook._UNDO.clear()
        self.assertFalse(wordbook.undo_last()["ok"])


class TestMigration(unittest.TestCase):
    def test_md_is_converted_to_txt(self):
        with tempfile.TemporaryDirectory() as d:
            md = os.path.join(d, "词表.md")
            txt = os.path.join(d, "词表.txt")
            with open(md, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("# 生词本\n\n> 说明\n\n## run\nv. 跑\n- **音标**：/rʌn/\n\n")
            wordbook.migrate_legacy_md(txt)
            self.assertTrue(os.path.exists(txt))
            with open(txt, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("run\nv. 跑\n音标：/rʌn/", body)
            self.assertNotIn("##", body)
            self.assertFalse(os.path.exists(md), "原 md 应改名")
            self.assertTrue(os.path.exists(md + ".bak"))

    def test_does_not_touch_existing_txt(self):
        with tempfile.TemporaryDirectory() as d:
            md = os.path.join(d, "词表.md")
            txt = os.path.join(d, "词表.txt")
            with open(md, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("## old\nv. 旧\n")
            with open(txt, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("new\nn. 新\n")
            wordbook.migrate_legacy_md(txt)
            with open(txt, encoding="utf-8") as fh:
                self.assertIn("new", fh.read())


class TestSameSite(unittest.TestCase):
    PORT = 8765

    def _check(self, headers: dict) -> bool:
        handler = object.__new__(wordbook.Handler)
        handler.headers = headers
        handler.server = types.SimpleNamespace(server_port=self.PORT)
        return handler._same_site()

    def test_same_origin_allowed(self):
        self.assertTrue(self._check({
            "Host": f"127.0.0.1:{self.PORT}",
            "Origin": f"http://127.0.0.1:{self.PORT}",
        }))

    def test_missing_origin_allowed(self):
        self.assertTrue(self._check({"Host": f"127.0.0.1:{self.PORT}"}))

    def test_cross_site_rejected(self):
        self.assertFalse(self._check({
            "Host": f"127.0.0.1:{self.PORT}",
            "Origin": "http://evil.example",
            "Sec-Fetch-Site": "cross-site",
        }))

    def test_dns_rebinding_rejected(self):
        self.assertFalse(self._check({
            "Host": "evil.example:8765",
            "Origin": "http://evil.example:8765",
            "Sec-Fetch-Site": "same-origin",
        }))

    def test_null_origin_rejected(self):
        self.assertFalse(self._check({
            "Host": f"127.0.0.1:{self.PORT}",
            "Origin": "null",
        }))


class TestPort(unittest.TestCase):
    def test_pick_port_skips_occupied(self):
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 18910))
        probe.listen(1)
        try:
            self.assertNotEqual(wordbook.pick_port(18910), 18910)
        finally:
            probe.close()

    def test_pick_port_returns_free_port(self):
        self.assertEqual(wordbook.pick_port(18920), 18920)

    @unittest.skipUnless(os.name == "nt", "Windows 的独占绑定语义")
    def test_second_server_cannot_bind(self):
        first = wordbook.Server(("127.0.0.1", 18930), wordbook.Handler)
        try:
            with self.assertRaises(OSError):
                wordbook.Server(("127.0.0.1", 18930), wordbook.Handler)
        finally:
            first.server_close()


class TestConfig(unittest.TestCase):
    def test_defaults_present(self):
        parsed = wordbook.load_config()
        self.assertIn("fields", parsed)
        self.assertTrue(parsed["fields"]["zh"])
        self.assertEqual(parsed["template"], "")
        self.assertEqual(parsed["lang"], "auto")

    def test_roundtrip(self):
        old = wordbook.CONFIG_PATH
        with tempfile.TemporaryDirectory() as d:
            wordbook.CONFIG_PATH = os.path.join(d, "config.json")
            try:
                wordbook.save_config({"fields": {"zh": False, "en": True}, "template": "{{word}}", "lang": "en"})
                parsed = wordbook.load_config()
                self.assertFalse(parsed["fields"]["zh"])
                self.assertTrue(parsed["fields"]["en"])
                self.assertEqual(parsed["template"], "{{word}}")
                self.assertEqual(parsed["lang"], "en")
            finally:
                wordbook.CONFIG_PATH = old


class TestI18n(unittest.TestCase):
    def test_label_sets_differ(self):
        self.assertEqual(wordbook.LABELS["zh"]["phonetic"], "音标")
        self.assertEqual(wordbook.LABELS["en"]["phonetic"], "Phonetic")

    def test_entry_labels_follow_lang(self):
        out_en = wordbook.format_entry(PAYLOAD, cfg(phonetic=True), lang="en")
        self.assertIn("Phonetic：", out_en)
        out_zh = wordbook.format_entry(PAYLOAD, cfg(phonetic=True), lang="zh")
        self.assertIn("音标：", out_zh)

    def test_resolve_lang_priority(self):
        self.assertEqual(wordbook.resolve_lang("en", "zh-CN", {"lang": "zh"}), "en")
        self.assertEqual(wordbook.resolve_lang(None, "en-US,en;q=0.9", {"lang": "zh"}), "en")
        self.assertEqual(wordbook.resolve_lang(None, None, {"lang": "zh"}), "zh")
        self.assertEqual(wordbook.resolve_lang(None, None, None), "zh")
        self.assertEqual(wordbook.resolve_lang("xx", None, None), "zh")

    def test_messages_localized(self):
        self.assertIn("Neither dictionary", wordbook.message("not_found", "en"))
        self.assertIn("两个词典", wordbook.message("not_found", "zh"))
        self.assertEqual(wordbook.message("unknown_key", "en"), "unknown_key")

    def test_config_roundtrips_lang(self):
        old = wordbook.CONFIG_PATH
        with tempfile.TemporaryDirectory() as d:
            wordbook.CONFIG_PATH = os.path.join(d, "config.json")
            try:
                wordbook.save_config({"fields": {}, "template": "", "lang": "en"})
                self.assertEqual(wordbook.load_config()["lang"], "en")
                wordbook.save_config({"fields": {}, "template": "", "lang": "bogus"})
                self.assertEqual(wordbook.load_config()["lang"], "auto")
            finally:
                wordbook.CONFIG_PATH = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
