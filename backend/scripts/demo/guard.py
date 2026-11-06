"""Safety rules for the demo commands. Pure functions: they look at the configuration and refuse BEFORE anything touches a database.

A destructive command needs ALL of: environment explicitly 'demo' · database name that is recognisably a demo database ·
the database name typed again on the command line · a local database host · (for reset) the demo marker, or an empty database.
No single environment variable can authorise a reset."""
import re
from urllib.parse import urlparse

DEMO_DB_RE = re.compile(r"(^|[_-])demo($|[_-])", re.I)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "", "db", "postgres"}      # 'db'/'postgres' are the docker-compose service names


class DemoGuardError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"REFUSED: {msg}")


def parse(url: str) -> tuple[str, str]:
    u = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    return (u.hostname or ""), (u.path or "").lstrip("/")


def check_demo_target(environment: str, database_url: str) -> str:
    """Common to init / verify / reset. Returns the database name."""
    host, name = parse(database_url)
    if environment == "production":
        raise DemoGuardError("MINTREE_ENVIRONMENT is 'production'. Demo commands never run against production.")
    if environment != "demo":
        raise DemoGuardError(f"MINTREE_ENVIRONMENT must be exactly 'demo' (it is '{environment}'). Set it explicitly for this command.")
    if not name or not DEMO_DB_RE.search(name):
        raise DemoGuardError(f"database '{name}' is not recognisably a demo database (its name must contain the word 'demo', e.g. 'mintree_demo').")
    if name.lower() in {"postgres", "template0", "template1"}:
        raise DemoGuardError(f"'{name}' is a system database.")
    return name


def check_reset(environment: str, database_url: str, confirm: str | None, *, has_marker: bool, is_empty: bool, allow_remote: bool = False) -> str:
    name = check_demo_target(environment, database_url)
    host, _ = parse(database_url)
    if host not in LOCAL_HOSTS and not allow_remote:
        raise DemoGuardError(f"database host '{host}' is not local. Reset only runs against a local demo database (pass --allow-remote-host to override deliberately).")
    if confirm != name:
        raise DemoGuardError(f"to reset, repeat the database name: --confirm-reset-demo-database {name}")
    if not (has_marker or is_empty):
        raise DemoGuardError(f"database '{name}' has data but no demo marker: it was not created by 'demo_data init'. Refusing to wipe it.")
    return name
