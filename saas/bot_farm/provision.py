"""
Provision a bot container for an approved user.

Called by the control plane when a user is approved AND has saved broker creds AND
has a valid payment method on file.

For MVP: uses local Docker. For production: replaces docker calls with a cloud
orchestration provider (Fly.io, Kubernetes, etc.).

Usage:
    python -m bot_farm.provision --user-id <UUID>
"""

import argparse
import logging
import os
import secrets
import subprocess

import psycopg

log = logging.getLogger("provision")


def provision(user_id: str) -> str:
    """Create and start a Docker container for this user. Returns container ID."""
    container_name = f"d1bot-{user_id[:8]}"

    # Generate a short-lived bot JWT (admin scope for /internal/* endpoints).
    # Replace this with a call to your JWT issuer in real code.
    bot_token = secrets.token_urlsafe(32)  # placeholder

    cmd = [
        "docker", "run", "-d",
        "--restart", "unless-stopped",
        "--name", container_name,
        "-e", f"USER_ID={user_id}",
        "-e", f"CONTROL_PLANE_URL={os.environ.get('CONTROL_PLANE_URL', 'http://host.docker.internal:8000')}",
        "-e", f"BOT_TOKEN={bot_token}",
        "-v", f"d1bot-{user_id[:8]}-data:/data",
        "d1bot:latest",
    ]
    log.info(f"Provisioning: {' '.join(cmd[:8])} ...")
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    container_id = result.stdout.strip()

    # Update DB
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.warning("DATABASE_URL not set — skipping DB update")
    else:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE user_configs
                       SET bot_status = 'running',
                           bot_container_id = %s,
                           bot_provisioned_at = NOW()
                       WHERE user_id = %s""",
                    (container_id, user_id),
                )
            conn.commit()

    log.info(f"Provisioned container {container_id} for user {user_id}")
    return container_id


def teardown(user_id: str):
    """Stop and remove a user's bot container. Optionally keep the data volume."""
    container_name = f"d1bot-{user_id[:8]}"
    subprocess.run(["docker", "stop", container_name], check=False)
    subprocess.run(["docker", "rm", container_name], check=False)
    log.info(f"Removed container {container_name}")

    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE user_configs SET bot_status = 'stopped',
                           bot_container_id = NULL
                       WHERE user_id = %s""",
                    (user_id,),
                )
            conn.commit()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("provision"); pv.add_argument("--user-id", required=True)
    td = sub.add_parser("teardown"); td.add_argument("--user-id", required=True)
    args = p.parse_args()

    if args.cmd == "provision":
        print(provision(args.user_id))
    else:
        teardown(args.user_id)
