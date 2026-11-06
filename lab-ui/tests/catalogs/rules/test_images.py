"""IM, images and names. For each code one failing fixture in which only that code fires, and one that passes; the message
equals. The Lab's seeder is the one named anchor: a playground with a seed service passes IM01 to IM04, and the
same playground without the anchor fails IM02 (without the exemption every playground with a seed would fail its own standard).
The rules read the Compose files of a tree as text, or the units a caller loaded."""

import unittest

from lab_ui.catalogs.rules import context, images
from tests.data.rules import support
from tests.data.rules.support import codes, run

DATA = support.HERE / "compose"
PREFIX = "ghcr.io/acme/hub"
SEEDER = "ghcr.io/lab/seeder"
CATALOG = support.CATALOG + f"  images: {PREFIX}\n"
UNIT = (DATA / "unit.yml").read_text()
HOW = {"seeder_images": (SEEDER,)}


def compose(*services, anchors=""):
    return f"{anchors}services:\n" + "".join(services)


def service(name, image=None, build=None, policy=True, extra=""):
    text = f"  {name}:\n"
    if image:
        text += f"    image: {image}\n"
    if build:
        text += f"    build: {build}\n"
    if policy:
        text += "    pull_policy: ${SHOP_PULL_POLICY:-build}\n"
    return text + extra


def found(*units, catalog=CATALOG, **how):
    """The findings on the good catalog with these units ({path: text}) and `catalog.yml` with the prefix."""
    files = {"catalog.yml": catalog, **dict(units)}
    return run(images, files, **{**HOW, **how})


def tuples(problems):
    return [(each.code, each.path, each.message) for each in problems]


class Rules(unittest.TestCase):

    def test_the_unit_fixture_passes_with_the_seeder_as_the_anchor(self):
        self.assertEqual([], found(("playgrounds/shop/compose.yml", UNIT)))

    def test_the_services_of_the_fixture_are_read_with_their_merge_keys_and_variables(self):
        listing = support.tree({"playgrounds/shop/compose.yml": UNIT})
        rows = {each.name: each for each in images.services_of_tree(listing) if each.path.startswith("playgrounds")}
        self.assertEqual({"shop-api", "shop-web", "shop-seed", "shop-db"}, set(rows))
        self.assertTrue(rows["shop-api"].built and rows["shop-api"].pull_policy and rows["shop-api"].context == "./apps/api")
        self.assertEqual("./apps/web", rows["shop-web"].context)
        self.assertEqual("Dockerfile", rows["shop-web"].dockerfile)
        self.assertEqual("../../../../../seeder", rows["shop-seed"].context)
        self.assertFalse(rows["shop-db"].built)
        self.assertEqual(("playground:shop", "playgrounds/shop"), (rows["shop-api"].unit, rows["shop-api"].folder))

    def test_im01_a_service_that_builds_has_no_pull_policy(self):
        message = "playgrounds/shop/compose.yml: shop-web builds an image but has no pull_policy. Fix: pull_policy: ${ID_PULL_POLICY:-build}."
        out = found(("playgrounds/shop/compose.yml", compose(service("shop-web", f"{PREFIX}/shop-web:1", "./apps/web", policy=False))))
        self.assertEqual([("IM01", "playgrounds/shop/compose.yml", message)], tuples(out))
        self.assertEqual(2, out[0].line)
        for build in ("./apps/web", "\n      context: ./apps/web\n      dockerfile: Dockerfile.dev"):
            out = found(("playgrounds/shop/compose.yml", compose(service("shop-web", f"{PREFIX}/shop-web:1", build, policy=False))))
            self.assertEqual(["IM01"], [each.code for each in out])
        out = found(("playgrounds/shop/compose.yml", compose(service("shop-web", build="./apps/web", policy=False))))
        self.assertEqual(["IM01"], [each.code for each in out], "a service that builds and names no image still needs a policy")

    def test_im01_the_seeders_service_too(self):
        text = compose(service("shop-seed", f"{SEEDER}:latest", "../../../../../seeder", policy=False))
        out = found(("playgrounds/shop/compose.yml", text))
        self.assertEqual(["IM01"], [each.code for each in out])

    def test_im01_passes_with_the_policy_from_an_alias_a_merge_or_a_value_of_any_kind(self):
        for extra in ("    pull_policy: never\n", "    pull_policy: always\n", "    pull_policy: ${X:-never}\n"):
            text = compose(service("shop-web", f"{PREFIX}/shop-web:1", "./apps/web", policy=False, extra=extra))
            self.assertEqual([], found(("playgrounds/shop/compose.yml", text)), extra)
        merged = compose(service("shop-web", f"{PREFIX}/shop-web:1", "./apps/web", policy=False, extra="    <<: *app\n"),
                         anchors="x-app: &app\n  pull_policy: build\n")
        self.assertEqual([], found(("playgrounds/shop/compose.yml", merged)))

    def test_im02_the_image_of_a_built_service_is_outside_the_prefix(self):
        message = "{}: the image {} is outside ghcr.io/acme/hub. Fix: name it ghcr.io/acme/hub/{}."
        cases = (("shop-web", "ghcr.io/other/shop-web:1", "shop-web"), ("web", "docker.io/web:latest", "shop-web"),
                 ("shop-web", "ghcr.io/acme/hubby/shop-web:1", "shop-web"), ("shop-web", "ghcr.io/acme/hub", "shop-web"),
                 ("shop-web", "${R}/shop-web:1", "shop-web"), ("shop-web", "shop-web", "shop-web"))
        for name, image, fixed in cases:
            with self.subTest(image=image):
                out = found(("playgrounds/shop/compose.yml", compose(service(name, image, "./apps/web"))))
                self.assertEqual([("IM02", "playgrounds/shop/compose.yml", message.format(name, image, fixed))], tuples(out), out)

    def test_im02_what_is_under_the_prefix_passes(self):
        for image in (f"{PREFIX}/shop-web:1", f"{PREFIX}/shop-web", f"{PREFIX}/shop-web@sha256:{'0' * 64}", f"{PREFIX.upper()}/shop-web:1",
                      f"${{SHOP_REGISTRY:-{PREFIX}}}/shop-web:${{SHOP_VERSION:-1}}", f"{PREFIX}/nested/deeper:1"):
            with self.subTest(image=image):
                self.assertEqual([], found(("playgrounds/shop/compose.yml", compose(service("shop-web", image, "./apps/web")))))

    def test_im02_without_a_prefix_nothing_is_outside(self):
        text = compose(service("shop-web", "ghcr.io/other/shop-web:1", "./apps/web"))
        self.assertEqual([], found(("playgrounds/shop/compose.yml", text), catalog=support.CATALOG))

    def test_im02_two_units_build_the_same_name(self):
        message = f"{PREFIX}/web is built by playground:shop and addon:web. Fix: name them {{unit}}-{{app}}."
        out = found(("playgrounds/shop/compose.yml", compose(service("shop-web", f"{PREFIX}/web:1", "./apps/web"))),
                    ("addons/web/compose.yml", compose(service("web", f"{PREFIX}/web:2", "."))))
        self.assertEqual([("IM02", "addons/web/compose.yml", message)], tuples(out), "the contexts differ and it is IM02's, not IM03's")

    def test_im02_each_extra_unit_is_said_once(self):
        units = [(f"addons/u{number}/compose.yml", compose(service(f"u{number}", f"{PREFIX}/same:1", "."))) for number in range(3)]
        out = found(*units)
        self.assertEqual(["IM02", "IM02"], [each.code for each in out])
        self.assertEqual(["addons/u1/compose.yml", "addons/u2/compose.yml"], [each.path for each in out])

    def test_im02_the_same_name_with_other_tags_or_in_other_case_is_the_same_name(self):
        out = found(("addons/a/compose.yml", compose(service("a", f"{PREFIX}/Same:1", "."))),
                    ("addons/b/compose.yml", compose(service("b", f"{PREFIX}/same:2", "."))))
        self.assertEqual(["IM02"], [each.code for each in out])

    def test_im03_one_name_two_builds(self):
        message = f"{PREFIX}/web is built from playgrounds/shop/apps/a and from playgrounds/shop/apps/b. Fix: one name, one build."
        out = found(("playgrounds/shop/compose.yml", compose(service("shop-a", f"{PREFIX}/web:1", "./apps/a"),
                                                              service("shop-b", f"{PREFIX}/web:2", "./apps/b"))))
        self.assertEqual([("IM03", "playgrounds/shop/compose.yml", message)], tuples(out))
        text = compose(service("shop-a", f"{PREFIX}/web:1", "\n      context: ./apps/a\n      dockerfile: Dockerfile"),
                       service("shop-b", f"{PREFIX}/web:1", "\n      context: ./apps/a\n      dockerfile: Dockerfile.dev"))
        out = found(("playgrounds/shop/compose.yml", text))
        self.assertEqual(f"{PREFIX}/web is built from playgrounds/shop/apps/a and from playgrounds/shop/apps/a Dockerfile.dev. Fix: one name, one build.",
                         out[0].message)

    def test_im03_one_build_for_one_name_passes(self):
        same = compose(service("shop-a", f"{PREFIX}/web:1", "./apps/a"), service("shop-b", f"{PREFIX}/web:2", "./apps/a/"),
                       service("shop-c", f"{PREFIX}/web:3", "\n      context: ./apps/a\n      dockerfile: Dockerfile"))
        self.assertEqual([], found(("playgrounds/shop/compose.yml", same)))
        only_run = compose(service("shop-a", f"{PREFIX}/web:1", "./apps/a"), service("shop-b", f"{PREFIX}/web:1"))
        self.assertEqual([], found(("playgrounds/shop/compose.yml", only_run)), "a service that runs the image builds nothing")

    def test_im04_images_is_the_registry_path_of_this_repository(self):
        message = "catalog.yml images is ghcr.io/acme/other; this repository publishes to ghcr.io/acme/hub. Fix: set images: ghcr.io/acme/hub."
        catalog = support.CATALOG + "  images: ghcr.io/acme/other\n"
        for how in ({"ci": True}, {"online": True}):
            out = run(images, {"catalog.yml": catalog}, repository="acme/hub", **how)
            self.assertEqual([("IM04", "catalog.yml", message)], tuples(out), how)
            self.assertEqual(9, out[0].line, "the line of images:")
        for catalog_ok in (support.CATALOG + "  images: ghcr.io/Acme/Hub\n", support.CATALOG + "  images: ghcr.io/acme/hub/\n"):
            self.assertEqual([], run(images, {"catalog.yml": catalog_ok}, repository="ACME/hub", ci=True))
        self.assertEqual([], run(images, {"catalog.yml": catalog}, repository="acme/hub"), "asked in CI or with --online only")
        self.assertEqual([], run(images, {"catalog.yml": catalog}, ci=True), "the repository is not known: nothing to compare with")
        self.assertEqual([], run(images, {"catalog.yml": support.CATALOG}, repository="acme/hub", ci=True), "no images, nothing published")


class Anchor(unittest.TestCase):
    """The seeder is the one named anchor: the Lab's own image, built from the Lab's own folder."""

    SEED = compose(service("shop-seed", f"{SEEDER}:latest", "../../../../../seeder"))

    def test_a_playground_with_a_seed_service_passes_all_four(self):
        out = found(("playgrounds/shop/compose.yml", self.SEED))
        self.assertEqual([], out)
        out = run(images, {"catalog.yml": CATALOG, "playgrounds/shop/compose.yml": self.SEED}, repository="acme/hub", ci=True, **HOW)
        self.assertEqual([], out)

    def test_without_the_anchor_the_seed_fails_its_own_standard(self):
        out = found(("playgrounds/shop/compose.yml", self.SEED), seeder_images=())
        self.assertEqual(["IM02"], [each.code for each in out])
        self.assertIn("is outside", out[0].message)

    def test_two_playgrounds_that_build_the_seeder_are_not_two_units_that_build_one_name(self):
        out = found(("playgrounds/one/compose.yml", self.SEED.replace("shop", "one")), ("playgrounds/two/compose.yml", self.SEED.replace("shop", "two")))
        self.assertEqual([], out)

    def test_the_seeder_still_needs_its_pull_policy(self):
        text = compose(service("shop-seed", f"{SEEDER}:latest", "../../../../../seeder", policy=False))
        self.assertEqual(["IM01"], [each.code for each in found(("playgrounds/shop/compose.yml", text))])

    def test_the_seeders_image_built_from_code_of_the_catalog_is_not_the_anchor(self):
        for build in ("./seed-app", "../../../../../seeder-fork", "../../seeder", "https://example.test/seeder.git#" + "a" * 40):
            with self.subTest(build=build):
                out = found(("playgrounds/shop/compose.yml", compose(service("shop-seed", f"{SEEDER}:latest", build))))
                self.assertEqual(["IM02"], [each.code for each in out], "a custom seed claims the Lab's image name")

    def test_the_seeders_context_with_an_image_of_another_name_is_not_the_anchor_either(self):
        text = compose(service("shop-seed", f"{PREFIX}/seed:1", "../../../../../seeder"))
        self.assertEqual([], found(("playgrounds/shop/compose.yml", text)), "inside the prefix, it is an ordinary built image")
        text = compose(service("shop-seed", "ghcr.io/other/seed:1", "../../../../../seeder"))
        self.assertEqual(["IM02"], [each.code for each in found(("playgrounds/shop/compose.yml", text))])

    def test_a_dockerfile_of_its_own_in_the_seeders_folder_is_not_the_anchor(self):
        text = compose(service("shop-seed", f"{SEEDER}:latest", "\n      context: ../../../../../seeder\n      dockerfile: Evil.Dockerfile"))
        self.assertEqual(["IM02"], [each.code for each in found(("playgrounds/shop/compose.yml", text))])

    def test_only_the_image_the_context_and_the_dockerfile_are_compared(self):
        """The other keys of a build are the trust policy's (T18 and N01): they neither make the seeder less of an anchor nor
        more."""
        for build in ("\n      context: ../../../../../seeder\n      args:\n        X: 1", "\n      context: ../../../../../seeder\n      target: base"):
            with self.subTest(build=build):
                self.assertEqual([], found(("playgrounds/shop/compose.yml", compose(service("shop-seed", f"{SEEDER}:latest", build)))))

    def test_the_engines_own_layout_has_the_seeder_at_the_top(self):
        text = compose(service("shop-seed", f"{SEEDER}:latest", "../../seeder"))
        self.assertEqual(["IM02"], [each.code for each in found(("playgrounds/shop/compose.yml", text))])
        self.assertEqual([], found(("playgrounds/shop/compose.yml", text), seeder_paths=("seeder",)))


class Reach(unittest.TestCase):
    """A unit is its `compose.yml`: a service takes what another service of the file brings through `extends`."""

    ROOT = "playgrounds/shop/compose.yml"
    BUILT = compose(service("shop-web", f"{PREFIX}/shop-web:1", "./apps/web", policy=False))

    def test_a_service_that_extends_a_service_of_its_own_file_or_a_chain_of_them(self):
        own = ("services:\n  base:\n    image: ghcr.io/acme/hub/shop-web:1\n    build: ./apps/web\n  shop-web:\n    extends: base\n"
               "  shop-api:\n    extends: shop-web\n    pull_policy: never\n")
        rows = images.services_of_tree(support.tree({"playgrounds/shop/compose.yml": own}))
        built = {each.name: (each.built, each.pull_policy, each.image) for each in rows if each.path.startswith("playgrounds")}
        self.assertEqual({"base": (True, False, "ghcr.io/acme/hub/shop-web:1"), "shop-web": (True, False, "ghcr.io/acme/hub/shop-web:1"),
                          "shop-api": (True, True, "ghcr.io/acme/hub/shop-web:1")}, built)

    def test_an_extends_that_goes_nowhere_never_loops(self):
        loop = "services:\n  a:\n    extends: b\n  b:\n    extends: a\n"
        rows = images.services_of_tree(support.tree({self.ROOT: loop}))
        self.assertEqual([(self.ROOT, "a"), (self.ROOT, "b")], [(each.path, each.name) for each in rows if each.path == self.ROOT])
        chain = "services:\n" + "".join(f"  s{number}:\n    extends: s{number + 1}\n" for number in range(200))
        self.assertEqual(200, len([each for each in images.services_of_tree(support.tree({self.ROOT: chain})) if each.path == self.ROOT]))

    def test_a_compose_file_with_a_nul_byte_is_read(self):
        text = b"\0\n" + self.BUILT.encode()
        out = found((self.ROOT, text))
        self.assertEqual(["IM01"], [each.code for each in out])


class Units(unittest.TestCase):
    """The units a caller has loaded with Compose, as JSON, instead of the files."""

    def units(self, services, file="playgrounds/shop/compose.yml"):
        return [{"kind": "playground", "id": "shop", "file": file, "config": {"services": services}}]

    def test_the_same_rules_over_the_resolved_services(self):
        listing = support.tree({"catalog.yml": CATALOG})
        services = {"shop-web": {"image": f"{PREFIX}/shop-web:1", "build": {"context": "/abs/playgrounds/shop/apps/web"}},
                    "shop-seed": {"image": f"{SEEDER}:latest", "build": {"context": "../../../../../seeder"}, "pull_policy": "build"},
                    "shop-db": {"image": "postgres:16"}}
        ctx = context(listing, units=self.units(services), **HOW)
        out = images.check(listing, ctx)
        self.assertEqual(["IM01"], [each.code for each in out], "the web service has no pull_policy")
        self.assertEqual("playgrounds/shop/compose.yml: shop-web builds an image but has no pull_policy. Fix: pull_policy: ${ID_PULL_POLICY:-build}.",
                         out[0].message)
        self.assertEqual(0, out[0].line, "a loaded unit has no line")

    def test_a_build_given_as_a_string_and_an_image_outside_the_prefix(self):
        listing = support.tree({"catalog.yml": CATALOG})
        ctx = context(listing, units=self.units({"api": {"image": "ghcr.io/other/api:1", "build": "./api", "pull_policy": "build"}}), **HOW)
        self.assertEqual(["IM02"], [each.code for each in images.check(listing, ctx)])

    def test_rows_that_are_not_services_are_left_alone(self):
        rows = images.services_of_units([{"config": None}, {"config": {"services": None}}, {"config": {"services": {"a": "x", "b": None}}},
                                         "nothing", {"file": "x"}])
        self.assertEqual([], rows)

    def test_the_units_decide_instead_of_the_files(self):
        broken = {"playgrounds/shop/compose.yml": compose(service("shop-web", "ghcr.io/other/x:1", "./apps/web", policy=False))}
        listing = support.tree({"catalog.yml": CATALOG, **broken})
        self.assertEqual({"IM01", "IM02"}, codes(images.check(listing, context(listing, **HOW))))
        self.assertEqual([], images.check(listing, context(listing, units=[], **HOW)))


class Helpers(unittest.TestCase):

    def test_the_repository_of_an_image(self):
        for image, repo in (("ghcr.io/Acme/Hub/App:1.2", "ghcr.io/acme/hub/app"), ("app", "app"), ("app:latest", "app"),
                            ("localhost:5000/app:1", "localhost:5000/app"), (f"app@sha256:{'0' * 64}", "app"),
                            ("${R:-ghcr.io/x}/app:${V:-1}", "ghcr.io/x/app"), ("${R:-${S:-ghcr.io/y}}/app", "ghcr.io/y/app"),
                            ("${R}/app", None), ("$R/app", None), ("", None), ("app:${V}", "app")):
            with self.subTest(image=image):
                self.assertEqual(repo, images.repository(image))

    def test_defaults_and_known(self):
        self.assertEqual("a-b", images.defaults("${X:-a}-${Y-b}"))
        self.assertEqual("a", images.known("${X:-a}"))
        self.assertIsNone(images.known("${X}"))
        self.assertIsNone(images.known("$X"))
        self.assertEqual("plain", images.known("plain"))

    def test_where_the_catalog_publishes(self):
        self.assertEqual("ghcr.io/acme/hub", images.publishes_to(" Acme/Hub "))


class Hostile(unittest.TestCase):

    PIECES = ["${", "${a-", "${a:-${b-", "$", "}", ":", "/", "@", "a", " ", "image: ", "build: ", "&a ", "*a", "x/", "pull_policy: ",
              "context: ", "\"", "'", "<<: *a ", "services: ", "- "]
    LINES = ["services:\n", "  a:\n", "    build: .\n", "<<: *a\n", "\n- ", "pull_policy: build\n", "      context: .\n", "\n",
             "include:\n  - a.yml\n", "    extends: a\n", "    extends: {file: a.yml, service: a}\n"]

    def test_every_form_the_reader_looks_for_finishes_in_under_a_second(self):
        for piece in self.PIECES:
            for head in ("services:\n  a:\n    image: ", "services:\n  a:\n    build: ", ""):
                text = head + piece * (1024 * 1024 // len(piece))
                listing = support.tree({"playgrounds/shop/compose.yml": text, "catalog.yml": CATALOG})
                ctx = context(listing, **HOW)
                best = min(support.timed(images.check, listing, ctx)[1] for _ in range(2))
                self.assertLess(best, 1.0, f"{head!r} + {piece[:20]!r}: {best:.2f} s")

    def test_a_megabyte_of_short_lines_is_read_in_proportion_to_its_size(self):
        for piece in self.LINES:
            text = "services:\n  a:\n    image: x\n" + piece * (1024 * 1024 // len(piece))
            listing = support.tree({"playgrounds/shop/compose.yml": text, "catalog.yml": CATALOG})
            ctx = context(listing, **HOW)
            best = min(support.timed(images.check, listing, ctx)[1] for _ in range(2))
            self.assertLess(best, 5.0, f"{piece!r}: {best:.2f} s")

    def test_an_image_of_a_megabyte_is_none(self):
        self.assertIsNone(images.repository("${a-" * 250000))
        self.assertEqual("a" * 100, images.repository("A" * 100))


if __name__ == "__main__":
    unittest.main()
