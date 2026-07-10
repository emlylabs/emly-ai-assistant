/// <reference types="vite/client" />

interface ImportMetaEnv {
    /** Backend root, e.g. `https://api.example.com` (no trailing slash). */
    readonly VITE_BACKEND_BASE_URL: string;
    /** Bot id or slug used to scope `/widget/{bot}/...` calls. */
    readonly VITE_BOT_ID: string;
    /** Days before the auto-generated end-user id is rotated. */
    readonly VITE_USER_ID_EXPIRY_DAYS: string;
}

interface ImportMeta {
    readonly env: ImportMetaEnv;
}
