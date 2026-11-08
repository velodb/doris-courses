"""HTML screenshots in saved working copies must use admitted content routes."""
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import unittest

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('notebook_images_runtime', ROOT / 'workshop/aws/runtime/multilingual.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.images.append(dict(attrs))


class NotebookHtmlImageTests(unittest.TestCase):
    def rewrite(self, text, callback):
        return runtime.notebook_html_images(text, callback, MarkdownIt('commonmark'))

    def test_actual_observability_screenshots_route_all_three_sources(self):
        path = ROOT / 'doris-course/03-observability/level1/module03-data-visualization/lab03_data_visualization.ipynb'
        notebook = json.loads(path.read_text())
        seen = []
        for cell in notebook['cells']:
            if cell['cell_type'] != 'markdown':
                continue
            text = ''.join(cell['source'])
            old = Images(); old.feed(text)
            new = Images(); new.feed(self.rewrite(text, lambda src: '/workshop/content/' + src + '?locale=kk-Cyrl'))
            self.assertEqual(len(old.images), len(new.images))
            for before, after in zip(old.images, new.images):
                self.assertEqual(after, {**before, 'src': '/workshop/content/' + before['src'] + '?locale=kk-Cyrl'})
                seen.append(before['src'])
        self.assertEqual(len(seen), 3)
        self.assertEqual({Path(p).name for p in seen}, {
            'discover-failed-checkouts.png', 'failed-checkout-trace.png', 'payment-incident-dashboard.png'})

    def test_code_examples_and_non_image_html_are_preserved(self):
        code = '```html\n<img src="images/example.png">\n```\n\n    <img src="images/indented.png">\n'
        inline = '`<img src="images/example.png">` and ``<img src="images/backtick.png">``\n'
        comment = '<!-- <img src="images/comment.png"> -->\n'
        link = '<a href="guide.md">guide</a>\n'
        source = code + '\n' + inline + comment + link + '<img src="images/example.png">\n'
        seen = []

        def route(src):
            seen.append(src)
            return '/content/' + src

        result = self.rewrite(source, route)
        self.assertTrue(result.startswith(code + '\n' + inline + comment + link))
        self.assertTrue(result.endswith('<img src="/content/images/example.png">\n'))
        self.assertEqual(seen, ['images/example.png'])

    def test_attribute_entities_queries_and_self_closing_tags_keep_their_meaning(self):
        source = '<IMG alt="A &amp; B" src=images/a.png?x=1&amp;y=2 width="800" />'
        result = self.rewrite(source, lambda src: '/workshop/content/' + src + '&locale=kk-Cyrl')
        parsed = Images(); parsed.feed(result)
        self.assertEqual(parsed.images, [{'alt': 'A & B', 'src': '/workshop/content/images/a.png?x=1&y=2&locale=kk-Cyrl', 'width': '800'}])
        self.assertIn('&amp;locale=', result)


if __name__ == '__main__':
    unittest.main()
