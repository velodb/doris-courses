"""Use the existing loopback Jupyter runtime behind the verified local gateway."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path("/usr/local/lib/doris-workshop")))
import serve

original = serve.make_config


def gateway_config(*args, **kwargs):
    config = original(*args, **kwargs)
    # Network security is enforced by loopback binding and the ALB-only gateway.
    # The gateway supplies canonical Host/proxy headers AFTER ownership checks.
    config.ServerApp.allow_remote_access = True
    config.ServerApp.trust_xheaders = True
    config.IdentityProvider.secure_cookie = True
    return config


if __name__ == "__main__":
    serve.make_config = gateway_config
    serve.main()
