# Component gallery

Every component of the Lab UI in every tone and state, light and dark side by side, drawn by the page's own code
(`web/js/components/ui.js`) and stylesheets. It is for reviewers, is not linked from the product and adds no dependency.

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory lab-ui   # then open http://localhost:8765/tests/gallery/
```

Check it at 2x zoom after a change to `web/css` or to a component of `ui.js`: [DESIGN.md](../../DESIGN.md#the-design-system).
