"""catalogs/kept.py: the person's settings that no item holds any more, kept in a folder of the Lab's own, shown, and deleted only
at the person's word."""

from lab_ui import paths
from lab_ui.catalogs import kept

from .. import Case


class Kept(Case):

    def setUp(self):
        self.folder(paths)

    def made(self, key, item="playgrounds/evil", text="EVIL_LEVEL=debug\n"):
        source = self.root / "old" / item
        source.mkdir(parents=True, exist_ok=True)
        (source / ".env").write_text(text)
        return kept.keep(key, "abc", [(source / ".env", f"{item}/.env")])

    def test_what_is_kept_is_listed_where_it_came_from_and_goes_at_the_persons_word(self):
        where, failed = self.made("evil")
        self.made(kept.LOCAL, "addons/scratch")
        self.assertEqual(failed, [])
        rows = kept.listed()
        self.assertEqual(sorted((row["from"], tuple(row["items"])) for row in rows), [("Personal", ("scratch",)), ("evil", ("evil",))])
        name = where.rsplit("/", 1)[-1]
        self.assertEqual(kept.remove(name), {})
        self.assertFalse((self.root / where).exists())
        self.assertNotIn("evil", kept.record()["kept"], "its line goes with it")
        for bad in ("../x", "evil", "", None, "evil-0a1b2c3d/../.."):
            with self.subTest(bad=bad), self.assertRaises(paths.NotFound):
                kept.remove(bad)

    def test_a_setting_never_goes_over_one_that_is_there_and_a_place_it_cannot_take_never_raises(self):
        source = self.root / "a" / "secrets"
        source.mkdir(parents=True)
        (source / "tls").write_text("a file\n")
        (self.root / "b").mkdir()
        (self.root / "b" / "secrets").write_text("a file where a folder must be\n")
        self.assertFalse(kept.carry(source / "tls", self.root / "b" / "secrets" / "tls"))
        self.assertTrue((source / "tls").is_file(), "it stays where it is")
        (self.root / "c").mkdir()
        (self.root / "c" / "tls").write_text("theirs\n")
        self.assertFalse(kept.carry(source / "tls", self.root / "c" / "tls"))
        self.assertEqual((self.root / "c" / "tls").read_text(), "theirs\n")

    def test_the_list_is_answered_whatever_goes_or_breaks_while_it_is_read(self):
        self.made("evil")
        base = self.root / kept.KEPT_DIR
        (base / "gone-0a1b2c3d").symlink_to(self.root / "nowhere")
        listed = kept.listed()
        self.assertEqual([row["from"] for row in listed], ["evil"])
