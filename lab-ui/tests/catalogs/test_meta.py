"""catalogs/meta.py: the x-gravitee block of a compose text, read and changed as YAML reads it, whatever else the file says."""

from lab_ui.catalogs import meta

from .. import Case

DECOYS = ('services:\n  evil-root:\n    image: busybox:1.36\n    environment:\n      A: "copy_of: x, a"\n      B: "copy_of: x, b"\n'
          '    labels: {copy_of: "@fixture/tour"}   # copy_of: tour\n')


class Block(Case):

    def test_a_key_is_read_in_the_block_only_in_either_style(self):
        for text in (DECOYS + 'x-gravitee:\n  kind: playground\n  name: Evil  # copy_of: no\n  copy_of: "tour"\n',
                     DECOYS + "x-gravitee: {kind: playground, name: 'a, copy_of: b', copy_of: tour}\n",
                     DECOYS + 'x-gravitee: {kind: playground,  # copy_of: no }\n  copy_of: tour,\n  name: x}\n',
                     "\"x-gravitee\": &meta\n  'copy_of': \"tour\"  # a comment\n" + DECOYS):
            with self.subTest(text=text[-60:]):
                self.assertEqual(meta.value(text, "copy_of"), "tour")
        self.assertIsNone(meta.value(DECOYS, "copy_of"), "a value, a label or a comment is no key of the block")
        self.assertIsNone(meta.value(DECOYS + "x-gravitee: {kind: addon}\n", "copy_of"))

    def test_every_copy_of_of_the_block_goes_and_nothing_else(self):
        for text in (DECOYS + 'x-gravitee:\n  kind: playground\n  copy_of: "tour"\n  copy_of: again\n  name: Evil\n',
                     DECOYS + "x-gravitee: {copy_of: tour, kind: playground, copy_of: again, name: Evil}\n",
                     DECOYS + 'x-gravitee: {kind: playground,  # a comment\n  copy_of: tour,\n  name: Evil}\n'):
            with self.subTest(text=text[-60:]):
                done = meta.drop(text, "copy_of", override=True)
                self.assertTrue(done.startswith(DECOYS), "the decoys are left as they are")
                self.assertIsNone(meta.value(done, "copy_of"))
                self.assertEqual((meta.value(done, "kind"), meta.value(done, "name")), ("playground", "Evil"))

    def test_a_block_that_takes_keys_from_elsewhere_says_none(self):
        merged = meta.drop("x-a: &a {copy_of: tour}\nx-gravitee: {<<: *a, kind: addon}\n", "copy_of", override=True)
        self.assertEqual(meta.value(merged, "copy_of"), "", "an explicit key wins over what a merge brings")
        lines = meta.drop("x-a: &a {copy_of: tour}\nx-gravitee:\n  <<: *a\n  kind: addon\n", "copy_of", override=True)
        self.assertEqual(meta.value(lines, "copy_of"), "")
        self.assertEqual(meta.drop("x-gravitee: *a\nservices: {}\n", "copy_of", override=True),
                         'x-gravitee: {<<: *a, copy_of: ""}\nservices: {}\n')

    def test_put_writes_the_key_first_in_the_block(self):
        self.assertEqual(meta.put("x-gravitee: {kind: addon, copy_of: old}\n", "copy_of", 'new"one'),
                         'x-gravitee: {copy_of: "newone", kind: addon}\n')
        self.assertEqual(meta.put("x-gravitee:\n  kind: addon\nservices: {}\n", "private_from", "Partner fixture"),
                         'x-gravitee:\n  private_from: "Partner fixture"\n  kind: addon\nservices: {}\n')
        self.assertIsNone(meta.put("services: {}\n", "copy_of", "x"))

    def test_a_megabyte_of_any_shape_is_read_and_changed_in_under_a_second(self):
        import time
        size = 1 << 20
        shapes = {"spaces": "x-gravitee:\n  a" + " " * size + "\n", "tabs": "x-gravitee: {a" + "\t" * size + "}\n",
                  "long key": "x-gravitee:\n  " + "k" * size + ": v\n", "spaces then a colon": "x-gravitee:\n  a" + " " * size + "b: c\n",
                  "lines": "x-gravitee:\n" + "   x\n" * (size // 5), "flow entries": "x-gravitee: {" + "a: b," * (size // 5) + "}\n"}
        for name, text in shapes.items():
            for call in (lambda: meta.value(text, "copy_of"), lambda: meta.drop(text, "copy_of", override=True),
                         lambda: meta.put(text, "private_from", "x")):
                with self.subTest(name):
                    started = time.monotonic()
                    call()
                    self.assertLess(time.monotonic() - started, 1.0)
