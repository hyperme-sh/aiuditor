import os

import uvicorn

from aiuditor_service.package import resolve_bind_host
from aiuditor_service.role import resolve_role


def main() -> None:
    from controlplane_api.metrics import start_metrics_server

    start_metrics_server()
    from aiuditor_service.app import app

    role = resolve_role()
    host = resolve_bind_host(role)
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
