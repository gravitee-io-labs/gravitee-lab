# Hello

A page that says it is running. It is the sample of this catalog: it shows the layout of an add-on and passes `make check` as it is.

| | |
|---|---|
| Starts | One container, `traefik/whoami`, with no setting to give |
| Open | http://localhost:8199 answers with the request it received |
| Setting | `__CATALOG_ENV___HELLO_VERSION` is the version of the image |

Delete this folder when the first real add-on lands, and copy it as the start of that one: rename the service and the container with the id of this catalog, and keep a loopback port.
