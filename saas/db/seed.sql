-- =====================================================================
-- Seed data — admin user + a sample test user (for local dev only)
-- DO NOT run against production. Generate real admin via secure script.
-- =====================================================================

-- Default admin (password: change immediately on first login)
-- Hash below is argon2id of "CHANGEME-on-first-login-now-2026"
-- Regenerate with: python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your_password'))"
INSERT INTO admin_users (admin_id, email, password_hash, role)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'admin@d1bot.local',
    '$argon2id$v=19$m=65536,t=3,p=4$REPLACE_ON_DEPLOY',
    'admin'
)
ON CONFLICT (email) DO NOTHING;
