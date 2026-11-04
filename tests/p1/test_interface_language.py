"""Every string the interface asks to translate must exist in Russian."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ("workspace.js", "dashboard.js", "store.js", "operations.js")


def dictionary():
    text = (ROOT / "web/i18n.ru.js").read_text(encoding="utf-8")
    keys = set()
    for match in re.finditer(r"""^\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"):""", text, re.M):
        keys.add((match.group(1) or match.group(2) or "").replace("\\'", "'"))
    return keys


def literals(source):
    """String literals inside the first argument of each t(...) call."""
    found = set()
    for start in (m.end() for m in re.finditer(r"(?<![\w.$])t\(", source)):
        depth, index, quote, current = 1, start, None, []
        while index < len(source) and depth:
            char = source[index]
            if quote:
                if char == "\\":
                    current.append(source[index + 1])
                    index += 1
                elif char == quote:
                    found.add("".join(current))
                    quote, current = None, []
                else:
                    current.append(char)
            elif char in "'\"":
                quote = char
            elif char == "`":
                break
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 1:
                break
            index += 1
    return found


class TranslationCoverageTests(unittest.TestCase):
    def test_every_interface_string_has_a_russian_translation(self):
        known = dictionary()
        missing = set()
        for name in SCRIPTS:
            source = (ROOT / "web" / name).read_text(encoding="utf-8")
            for text in literals(source):
                # Lower-case single words here are compared values, not interface text.
                if re.fullmatch(r"[a-z_-]+", text):
                    continue
                if text.strip() and re.search(r"[A-Za-z]", text) and text not in known:
                    missing.add(text)
        self.assertEqual(sorted(missing), [])

    def test_plural_forms_have_three_cases(self):
        text = (ROOT / "web/i18n.ru.js").read_text(encoding="utf-8")
        for match in re.finditer(r":\s*\[(.*?)\],?$", text, re.M):
            self.assertEqual(len(re.findall(r"'(?:[^'\\]|\\.)*'", match.group(1))), 3, match.group(0))

    def test_static_markup_is_translated(self):
        from html.parser import HTMLParser

        known = dictionary()

        class Texts(HTMLParser):
            def __init__(self):
                super().__init__()
                self.skip = 0
                self.rows = set()

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "kbd", "code"):
                    self.skip += 1
                for name, value in attrs:
                    if name in ("placeholder", "aria-label", "title") and value:
                        self.rows.add(value)

            def handle_endtag(self, tag):
                if tag in ("script", "style", "kbd", "code"):
                    self.skip -= 1

            def handle_data(self, data):
                text = data.strip()
                if text and not self.skip:
                    self.rows.add(text)

        parser = Texts()
        markup = (ROOT / "web/index.html").read_text(encoding="utf-8")
        # The help panel is replaced as a whole because its sentences carry inline tags.
        parser.feed(re.sub(r'<section id="help-panel".*?</section>', "", markup, flags=re.S))
        untranslatable = {"sentient", "Sentient · Store floor intelligence", "EN", "720p", "CSV", "MP4", "—", "0", "/path/to/camera.mp4", "0.97", "5s", "12s", "30s", "60s", "0.5×", "1×", "2×", "−0.2s", "+0.2s"}
        missing = sorted(t for t in parser.rows if re.search(r"[A-Za-z]", t) and t not in known and t not in untranslatable)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
