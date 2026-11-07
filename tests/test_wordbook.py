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


class TestMarkdown(unittest.TestCase):
    def test_default_is_two_lines(self):
        out = wordbook.to_markdown(PAYLOAD, cfg())
        lines = out.rstrip("\n").splitlines()
        self.assertEqual(lines[0], "## serendipity")
        self.assertEqual(lines[1], "n. 意外发现美好事物的运气，机缘巧合")
        self.assertEqual(len(lines), 2, "默认只应该有两行")

    def test_pos_off(self):
        out = wordbook.to_markdown(PAYLOAD, cfg(pos=False))
        self.assertEqual(out.splitlines()[1], "意外发现美好事物的运气，机缘巧合")

    def test_zh_off_drops_chinese_line(self):
        out = wordbook.to_markdown(PAYLOAD, cfg(zh=False, en=True))
        self.assertNotIn("意外发现", out)
        self.assertIn("- **英文**", out)

    def test_extra_fields(self):
        out = wordbook.to_markdown(
            PAYLOAD, cfg(phonetic=True, examples=True, forms=True, exam=True, web=True, time=True)
        )
        for marker in ("- **音标**", "- **例句**", "- **变形**", "- **考试**", "- **网络释义**", "- **收录**"):
            self.assertIn(marker, out)

    def test_multi_pos_keeps_pairing(self):
        line = wordbook.to_markdown(MULTI_POS, cfg()).splitlines()[1]
        self.assertEqual(line, "adj. 短暂的；n. 短生植物")

    def test_template_with_spaces(self):
        out = wordbook.to_markdown(PAYLOAD, {"fields": {}, "template": "## {{ word }}\n- {{ zh }}"})
        self.assertIn("## serendipity", out)
        self.assertNotIn("{{", out)

    def test_template_gets_heading_added(self):
        out = wordbook.to_markdown(PAYLOAD, {"fields": {}, "template": "{{word}} | {{zh}}"})
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "## serendipity", "模板缺 ## 时要自动补，否则判重和导出都认不出来")
        self.assertTrue(lines[1].startswith("serendipity | "))

    def test_template_keeps_existing_heading(self):
        out = wordbook.to_markdown(PAYLOAD, {"fields": {}, "template": "## {{word}}\n- {{zh}}"})
        self.assertEqual(out.count("## serendipity"), 1)


class TestCsv(unittest.TestCase):
    def test_formula_injection_is_escaped(self):
        self.assertEqual(wordbook._csv_safe("=1+1"), "'=1+1")
        self.assertEqual(wordbook._csv_safe("+1"), "'+1")
        self.assertEqual(wordbook._csv_safe("@x"), "'@x")
        self.assertEqual(wordbook._csv_safe("普通"), "普通")

    def test_export_skips_examples(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# 生词本\n\n## serendipity\nn. 意外发现\n- **音标**：x\n  - example sentence\n")
            old = wordbook.NOTES_PATH
            wordbook.NOTES_PATH = path
            try:
                text = wordbook.export_anki_csv().decode("utf-8-sig")
            finally:
                wordbook.NOTES_PATH = old
            self.assertIn("serendipity,n. 意外发现", text)
            self.assertNotIn("example sentence", text)


class TestUndo(unittest.TestCase):
    def _seed(self, directory: str):
        path = os.path.join(directory, "notes.md")
        # newline="" 很关键：默认文本模式在 Windows 会把 \n 翻成 \r\n，
        # 字节数就和 HEADER.encode() 对不上，撤销会（正确地）拒绝。
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(wordbook.HEADER)
        block = "## lucid\nadj. 明晰的\n\n"
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(block)
        wordbook._UNDO.clear()
        wordbook._UNDO.update({
            "path": path,
            "size": len(wordbook.HEADER.encode("utf-8")),
            "word": "lucid",
            "block": block,
            "after": os.path.getsize(path),
        })
        return path

    def test_undo_removes_last_entry(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._seed(d)
            result = wordbook.undo_last()
            self.assertTrue(result["ok"])
            self.assertFalse(os.path.exists(path), "撤到只剩标题头时应该把文件收掉")

    def test_undo_refuses_after_manual_edit(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._seed(d)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("## manual\n手写的\n\n")
            result = wordbook.undo_last()
            self.assertFalse(result["ok"])
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("## manual", body)
            self.assertIn("## lucid", body)

    def test_undo_without_state(self):
        wordbook._UNDO.clear()
        self.assertFalse(wordbook.undo_last()["ok"])


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

    def test_roundtrip(self):
        old = wordbook.CONFIG_PATH
        with tempfile.TemporaryDirectory() as d:
            wordbook.CONFIG_PATH = os.path.join(d, "config.json")
            try:
                wordbook.save_config({"fields": {"zh": False, "en": True}, "template": "{{word}}"})
                parsed = wordbook.load_config()
                self.assertFalse(parsed["fields"]["zh"])
                self.assertTrue(parsed["fields"]["en"])
                self.assertEqual(parsed["template"], "{{word}}")
            finally:
                wordbook.CONFIG_PATH = old


class TestI18n(unittest.TestCase):
    def test_label_sets_differ(self):
        self.assertEqual(wordbook.LABELS["zh"]["phonetic"], "音标")
        self.assertEqual(wordbook.LABELS["en"]["phonetic"], "Phonetic")

    def test_headers_are_localized(self):
        self.assertTrue(wordbook.HEADERS["zh"].startswith("# 生词本"))
        self.assertTrue(wordbook.HEADERS["en"].startswith("# Wordbook"))
        self.assertEqual(wordbook.HEADER, wordbook.HEADERS["zh"])

    def test_markdown_labels_follow_lang(self):
        out = wordbook.to_markdown(PAYLOAD, cfg(phonetic=True), lang="en")
        self.assertIn("- **Phonetic**：", out)
        out_zh = wordbook.to_markdown(PAYLOAD, cfg(phonetic=True), lang="zh")
        self.assertIn("- **音标**：", out_zh)

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
