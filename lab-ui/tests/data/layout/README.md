# Layout migration data

The golden texts of `tests/library/test_layout.py`: the compose files of four items of a machine's own, as they were before the
catalogs and as they are once `layout.py` has moved them to `.lab/catalogs/local/`. They hold the structure of the files
the real migration was first tried on (a playground that includes a setup of its own and an add-on of the default
catalog and builds the seeder; a setup that includes add-ons of the default catalog), with the unit names of the
fixture catalog. Nothing in them is real: no item, address or organization.

```
legacy/       the file where the old layout kept it
  playgrounds/local/trip-demo/compose.yml    includes setups/local/duo.yml and addons/echo, builds ../../../seeder
  playgrounds/local/relay/compose.yml        builds ../../../seeder
  setups/local/duo.yml                       includes two add-ons of the default catalog
  setups/local/trio.yml                      includes a setup of its own folder and an add-on of the default catalog
expected/     the same file under .lab/catalogs/local/, at the path the new layout gives it
```

`expected/` has the same relative paths as `legacy/`, so that the test compares each file with its twin. The lines that
change are the ones of the table of the moves; a `command` with `..` in it and a path that stays in the
item's folder do not change.

The rest of the migration's synthetic tree (a clone after the pull of the split: 12 units, their machine files, the on
file, the stars, the tested sets and a platform copy) is built by `Machine.build` in `test_layout.py`, since it is
written in a temporary folder and changed by each test.
