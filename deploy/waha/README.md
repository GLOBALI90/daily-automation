# WAHA on an Always-Free ARM VM

This deployment target is an Oracle Cloud Always Free ARM VM.

## Target sizing

Use the current Oracle Always Free Ampere A1 allocation within the documented limit:
2 OCPUs and up to 12 GB RAM total. WAHA recommends at least 2 CPU / 4 GB RAM for a single session.

## Deployment

1. Provision an Ubuntu ARM64 VM with a public IP.
2. Install Docker and Docker Compose.
3. Copy `.env.example` to `.env` and set strong values.
4. Run:
   `docker compose up -d`
5. Put HTTPS in front of port 3000 (for example with Caddy or another reverse proxy).
6. Open the WAHA dashboard over HTTPS and connect the existing WhatsApp session.
7. Persist `./sessions` and back it up before upgrades.

Do not commit `.env` or WhatsApp session data to Git.

## Important

The Oracle VM is intended to replace the sleeping Blitz WAHA host. The GitHub automation should call the HTTPS WAHA endpoint only after the server health check passes.

This branch is deployment preparation only; it does not provision an Oracle account or VM.
