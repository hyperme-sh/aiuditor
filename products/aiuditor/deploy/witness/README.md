# AIuditor witness (host / door package)

Same `aiuditor-service` image. `AIUDITOR_ROLE=witness`. It hydrates a tenant snapshot from the Azure global parent and never fulfills or mints a market ticket.

Not the Go host agent. Not AImmune. Not on hyperme.sh. Handoff (spine D) is not this path.

## Pin

| Env | Rule |
|---|---|
| `AIUDITOR_ROLE` | `witness` |
| `HOST` | `127.0.0.1` (default). `0.0.0.0` fails boot. |
| `PORT` | `8015` on the box (loopback only) |
| `AIUDITOR_PARENT_URL` | Global AIuditor base (test: `https://api.test.hyperme.sh`) |
| `AIUDITOR_PARENT_TOKEN` | Must match parent `AIUDITOR_INTERNAL_TOKEN` |
| `AIUDITOR_HYDRATE_TENANT_ID` | Required when parent URL is set |
| `AIUDITOR_HYDRATE_EVERY` | Seconds (default 60, floor 5) |
| `DATABASE_URL` | Omit on the box. Witness keeps a memory copy from hydrate. |

`GET /api/v1/aiuditor/meta` advertises `writer=none`. Hypermesh marketplace checkout already refuses that writer.

## Run on the box

`--network host` plus `HOST=127.0.0.1` so the process is loopback-only. Do not publish `0.0.0.0:8015`.

```bash
sudo install -d -m 0700 /etc/hypermesh
sudo cp witness.env.example /etc/hypermesh/aiuditor-witness.env
sudo chmod 0600 /etc/hypermesh/aiuditor-witness.env
# set PARENT_TOKEN and HYDRATE_TENANT_ID
sudo cp aiuditor-witness.service /etc/systemd/system/
sudo systemctl enable --now aiuditor-witness
```

Health: `curl http://127.0.0.1:8015/api/v1/health/`

The timer pulls `GET {parent}/api/v1/aiuditor/hydrate` with `X-Internal-Token` and `X-Tenant-ID`. Prompt bodies and HMAC tickets stay off that snapshot.
