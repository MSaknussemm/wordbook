"""wordbook 的单元测试。

全部离线：不查词典、不起网络，只测纯逻辑和 Windows 上的绑定行为。
运行： python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wordbook  # noqa: E402
import windows_launcher  # noqa: E402

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

    def test_single_line_template_still_parses(self):
        """{{word}} | {{zh}} 只产出一行时，单词行仍要自动补齐，否则解析器会把整行当单词。"""
        out = wordbook.format_entry(PAYLOAD, {"fields": {}, "template": "{{word}} | {{zh}}"})
        entries = wordbook.parse_entries(out)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][0], "serendipity", "第一行必须是单词本身")
        self.assertIn("|", entries[0][1])


    def test_template_with_blank_line_is_collapsed(self):
        out = wordbook.format_entry(PAYLOAD, {"fields": {}, "template": "{{word}}\n\n{{zh}}"})
        self.assertEqual(len(wordbook.parse_entries(out)), 1, "空行会被当成分隔符，必须压掉")
        self.assertNotIn("\n\n", out.strip())

    def test_sanitize_block(self):
        self.assertEqual(wordbook._sanitize_block("a\n\n\nb\n"), "a\nb")
        self.assertEqual(wordbook._sanitize_block("\n\na\nb\n\n"), "a\nb")


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

    def test_bom_file_still_dedupes(self):
        """记事本另存为 UTF-8 with BOM 后，第一条不能被当成新词。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
                fh.write("apple\nn. 苹果\n\n")
            self.assertTrue(os.path.exists(path))
            self.assertTrue(wordbook.is_saved("apple", path))
            self.assertEqual(wordbook.parse_entries(wordbook.read_notes(path))[0][0], "apple")

    def test_gbk_file_is_readable(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            with open(path, "wb") as fh:
                fh.write("apple\nn. 苹果\n".encode("gbk"))
            self.assertTrue(wordbook.is_saved("apple", path))


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

    def test_undo_works_when_separator_was_added(self):
        """保存时补过一个空行，撤销仍要能对上（基线是正文起点，不是补行之前）。"""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            head = "run\nv. 跑"
            prefix = "\n"
            block = "apple\nn. 苹果\n\n"
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(head + prefix + block)
            wordbook._UNDO.clear()
            wordbook._UNDO.update({
                "path": path,
                "size": len((head + prefix).encode("utf-8")),
                "word": "apple",
                "block": block,
                "after": os.path.getsize(path),
            })
            result = wordbook.undo_last()
            self.assertTrue(result["ok"])
            with open(path, encoding="utf-8") as fh:
                # 撤销回到「本条正文起点」，也就是补的那个空行之后
                self.assertEqual(fh.read(), head + prefix)

    def test_undo_reports_remaining_count(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            head = "run\nv. 跑\n\n"
            block = "lucid\nadj. 明晰的\n\n"
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(head + block)
            wordbook._UNDO.clear()
            wordbook._UNDO.update({
                "path": path,
                "size": len(head.encode("utf-8")),
                "word": "lucid",
                "block": block,
                "after": os.path.getsize(path),
            })
            result = wordbook.undo_last()
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 1, "撤掉一条后应剩 1 条，不是 0")

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


class TestLookupErrors(unittest.TestCase):
    """网络炸了不等于「词典没收录」——两者要能区分。"""

    def setUp(self):
        wordbook._cache.clear()
        self._real = wordbook.fetch_json

    def tearDown(self):
        wordbook.fetch_json = self._real
        wordbook._cache.clear()

    def test_network_failure_is_not_not_found(self):
        def boom(url, timeout=0):
            raise urllib.error.URLError("simulated")
        wordbook.fetch_json = boom
        with self.assertRaises(wordbook.LookupNetworkError):
            wordbook.lookup("zzznetonly")

    def test_http_503_is_service_error(self):
        def boom(url, timeout=0):
            raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
        wordbook.fetch_json = boom
        with self.assertRaises(wordbook.LookupNetworkError):
            wordbook.lookup("zzz503only")

    def test_empty_results_are_not_found(self):
        wordbook.fetch_json = lambda url, timeout=0: {}
        with self.assertRaises(LookupError) as ctx:
            wordbook.lookup("zzzmissonly")
        self.assertNotIsInstance(ctx.exception, wordbook.LookupNetworkError)


class TestEncoding(unittest.TestCase):
    def test_gbk_file_is_converted_on_write(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            with open(path, "wb") as fh:
                fh.write("apple\nn. 苹果\n".encode("gbk"))
            self.assertTrue(wordbook.ensure_utf8(path))
            with open(path, "rb") as fh:
                raw = fh.read()
            raw.decode("utf-8")                      # 不抛异常就说明已经是 UTF-8
            self.assertTrue(os.path.exists(path + ".bak"))

    def test_utf8_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("apple\nn. 苹果\n")
            self.assertFalse(wordbook.ensure_utf8(path))

    def test_bom_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "notes.txt")
            with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
                fh.write("apple\nn. 苹果\n")
            self.assertFalse(wordbook.ensure_utf8(path))


class TestLookupCache(unittest.TestCase):
    def test_saved_is_not_cached(self):
        """收录状态不能进缓存——用户随时可能在别处删词。"""
        real = wordbook.lookup_youdao
        wordbook.lookup_youdao = lambda w: {
            "word": "zzcache", "entries": [], "examples": [], "forms": [],
            "exam": [], "web": [], "uk": "", "us": "", "phonetic": "", "source": "test",
        }
        try:
            out = wordbook.lookup("zzcache")
        finally:
            wordbook.lookup_youdao = real
            wordbook._cache.clear()
        self.assertNotIn("saved", out)


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
    def test_bind_server_skips_occupied_and_keeps_socket(self):
        with wordbook.Server(("127.0.0.1", 18910), wordbook.Handler):
            with wordbook.bind_server(18910) as server:
                self.assertGreater(server.server_port, 18910)
                with self.assertRaises(OSError):
                    wordbook.Server(server.server_address, wordbook.Handler)

    def test_bind_server_accepts_os_assigned_port(self):
        with wordbook.bind_server(0) as server:
            self.assertGreater(server.server_port, 0)

    def test_http_version_matches_application_version(self):
        self.assertEqual(wordbook.Handler.server_version, f"Wordbook/{wordbook.__version__}")

    @unittest.skipUnless(os.name == "nt", "Windows 的独占绑定语义")
    def test_second_server_cannot_bind(self):
        first = wordbook.Server(("127.0.0.1", 18930), wordbook.Handler)
        try:
            with self.assertRaises(OSError):
                wordbook.Server(("127.0.0.1", 18930), wordbook.Handler)
        finally:
            first.server_close()


class TestSingleInstance(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows 命名互斥体")
    def test_second_acquire_fails(self):
        name = "Local\\WordbookTest-%d" % os.getpid()
        self.assertTrue(wordbook.acquire_single_instance(name))
        self.assertFalse(wordbook.acquire_single_instance(name), "第二个实例必须被挡住")


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


class TestCustomNotes(unittest.TestCase):
    def setUp(self):
        self.directory = pathlib.Path(self.enter_patch(tempfile.TemporaryDirectory()))
        self.custom = self.directory / "已有词表.md"
        self.custom.write_text("existing\nExisting definition\n", encoding="utf-8")
        self.default = self.directory / "词表.txt"
        self.enter_patch(patch.multiple(
            wordbook, CONFIG_PATH=str(self.directory / "config.json"),
            NOTES_PATH=str(self.default), _CLI_NOTES_PATH=False, _CUSTOM_NOTES_ACTIVE=False,
            _SERVER=None, _UNDO={},
        ))
        self.enter_patch(patch.object(wordbook, "default_notes_path", return_value=str(self.default)))

    def enter_patch(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value

    def start(self, *args):
        with patch.object(sys, "argv", ["wordbook.py", "--port", "0", "--no-browser", *args]), \
                patch.object(wordbook, "bind_server", return_value=Mock(server_port=12345)), \
                patch.object(wordbook, "_write_port_file"), patch.object(wordbook.threading, "Thread"):
            wordbook.main()

    def config_response(self, payload=None):
        handler = object.__new__(wordbook.Handler)
        handler.path = "/api/config"
        handler.server = types.SimpleNamespace(server_port=12345)
        handler.headers = {"Host": "127.0.0.1:12345"}
        handler._json = Mock()
        if payload is None:
            handler.do_GET()
        else:
            body = json.dumps(payload).encode("utf-8")
            handler.headers["Content-Length"] = str(len(body))
            handler.rfile = io.BytesIO(body)
            handler.do_POST()
        self.assertEqual(handler._json.call_args.args[0], 200)
        return handler._json.call_args.args[1]

    def test_partial_settings_preserve_unsubmitted_values(self):
        wordbook.save_config({"fields": {"web": True, "zh": False}, "template": "{{word}} -- {{zh}}", "lang": "en"})
        self.config_response({"notes_path": str(self.custom)})
        wordbook.save_config({"fields": {"en": True}})
        config = wordbook.load_config()
        self.assertEqual(config["notes_path"], str(self.custom))
        self.assertEqual(config["template"], "{{word}} -- {{zh}}")
        self.assertEqual(config["lang"], "en")
        self.assertTrue(config["fields"]["web"])
        self.assertTrue(config["fields"]["en"])
        self.assertFalse(config["fields"]["zh"])

    def test_existing_custom_file_is_selected_at_startup(self):
        wordbook.save_config({"notes_path": str(self.custom)})
        self.start()
        response = self.config_response()
        self.assertEqual(response["path"], str(self.custom))
        self.assertFalse(response["notes_path_locked"])
        self.assertFalse(response["notes_path_fallback"])

    def test_missing_at_startup_uses_default_and_preserves_selection(self):
        wordbook.save_config({"notes_path": str(self.custom)})
        self.custom.unlink()
        self.start()
        response = self.config_response()
        self.assertTrue(response["notes_path_fallback"])
        self.assertEqual(response["path"], str(self.default))
        self.assertEqual(response["config"]["notes_path"], str(self.custom))
        with patch.object(wordbook, "lookup", return_value=PAYLOAD):
            self.assertTrue(wordbook.save_word("serendipity")["ok"])
        self.assertTrue(self.default.is_file())
        self.assertFalse(self.custom.exists())

    def test_file_restored_after_fallback_can_be_selected_again(self):
        wordbook.save_config({"notes_path": str(self.custom)})
        self.custom.unlink()
        self.start()
        self.custom.touch()
        wordbook.save_config({"notes_path": str(self.custom)})
        self.assertEqual(wordbook.NOTES_PATH, str(self.custom))
        self.assertFalse(self.config_response()["notes_path_fallback"])

    def test_missing_during_lookup_is_not_recreated(self):
        wordbook.save_config({"notes_path": str(self.custom)})

        def disconnect(_word):
            self.custom.unlink()
            return PAYLOAD

        with patch.object(wordbook, "lookup", side_effect=disconnect) as lookup:
            self.assertEqual(wordbook.save_word("serendipity")["code"], "notes_missing")
            self.assertEqual(wordbook.save_word("serendipity")["code"], "notes_missing")
            lookup.assert_called_once()
        self.assertFalse(self.custom.exists())
        self.assertFalse(self.default.exists())

    def test_missing_parent_at_runtime_prompts_reselection(self):
        folder = self.directory / "removable"
        folder.mkdir()
        file = folder / "words.txt"
        file.touch()
        wordbook.save_config({"notes_path": str(file)})
        file.unlink()
        folder.rmdir()
        self.assertEqual(wordbook.save_word("serendipity")["code"], "notes_missing")

    def test_cli_override_locks_path_without_changing_selection(self):
        wordbook.save_config({"notes_path": str(self.custom)})
        cli_file = self.directory / "CLI.txt"
        self.start("--file", str(cli_file))
        wordbook.save_config({"notes_path": "", "lang": "en"})
        response = self.config_response()
        self.assertTrue(response["notes_path_locked"])
        self.assertFalse(response["notes_path_fallback"])
        self.assertEqual(response["path"], str(cli_file))
        self.assertEqual(response["config"]["notes_path"], str(self.custom))
        self.assertEqual(response["config"]["lang"], "en")


class TestInstalledStartup(unittest.TestCase):
    def test_frozen_default_without_desktop_uses_state_directory(self):
        with patch.object(sys, "frozen", True, create=True), \
                patch.dict(os.environ, {"LOCALAPPDATA": r"C:\TestState"}), \
                patch.object(os.path, "isdir", return_value=False):
            self.assertEqual(wordbook.default_notes_path(),
                             os.path.join(r"C:\TestState", "Wordbook", "词表.txt"))

    def test_secondary_launcher_waits_past_previous_eight_second_limit(self):
        port = Mock()
        port.read_text.side_effect = [FileNotFoundError(), "8765"]
        connection = Mock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'{"ok":true,"app":"wordbook"}'
        with patch.object(windows_launcher, "Path", return_value=port), \
                patch.object(windows_launcher.time, "monotonic", side_effect=[0, 0, 9]), \
                patch.object(windows_launcher.time, "sleep"), \
                patch.object(windows_launcher.http.client, "HTTPConnection", return_value=connection), \
                patch.object(windows_launcher, "open_browser") as launch:
            windows_launcher.reopen_running_instance()
        launch.assert_called_once_with("http://127.0.0.1:8765/")

    def test_missing_default_browser_reports_manual_address(self):
        with patch.object(windows_launcher.os, "startfile", side_effect=OSError, create=True), \
                patch.object(windows_launcher.ctypes, "windll", create=True) as windll:
            windows_launcher.open_browser("http://127.0.0.1:8765/")
        self.assertIn("http://127.0.0.1:8765/", windll.user32.MessageBoxW.call_args.args[1])


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
