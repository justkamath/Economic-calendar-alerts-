"""Who gets alerts, and what each person asked for.

Every subscriber chooses an impact level and (optionally) a list of currencies.
The list is saved ENCRYPTED in subscribers.enc so it can live in a public repo;
the key is the SUBSCRIBERS_KEY secret. Chat IDs are never printed to logs.

Encryption uses only Python's standard library: HMAC-SHA256 as a keystream
(counter mode) with an HMAC tag over the result (encrypt-then-MAC). A wrong
key or a damaged file is detected and the file is never overwritten.
"""
import base64
import hashlib
import hmac
import json
import os

import config as cfg

MAGIC = b"ecal1"
NONCE_BYTES = 16
TAG_BYTES = 32


class SubscriberError(RuntimeError):
    pass


def _keys(secret):
    if len(secret) < 24:
        raise SubscriberError("SUBSCRIBERS_KEY must be at least 24 characters.")
    raw = hashlib.sha256(secret.encode("utf-8")).digest()
    return (hmac.new(raw, b"encrypt", hashlib.sha256).digest(),
            hmac.new(raw, b"authenticate", hashlib.sha256).digest())


def _stream(key, nonce, length):
    blocks, counter = [], 0
    while sum(len(b) for b in blocks) < length:
        blocks.append(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:length]


def encrypt(secret, plaintext):
    enc_key, mac_key = _keys(secret)
    nonce = os.urandom(NONCE_BYTES)
    body = bytes(a ^ b for a, b in zip(plaintext, _stream(enc_key, nonce, len(plaintext))))
    tag = hmac.new(mac_key, MAGIC + nonce + body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(MAGIC + nonce + tag + body)


def decrypt(secret, blob):
    enc_key, mac_key = _keys(secret)
    try:
        raw = base64.urlsafe_b64decode(blob)
    except Exception:
        raise SubscriberError("Subscribers file is damaged.") from None
    head = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    if len(raw) < head or not raw.startswith(MAGIC):
        raise SubscriberError("Subscribers file is damaged.")
    nonce = raw[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    tag = raw[len(MAGIC) + NONCE_BYTES:head]
    body = raw[head:]
    good = hmac.new(mac_key, MAGIC + nonce + body, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, good):
        raise SubscriberError("Cannot decrypt subscribers file - is SUBSCRIBERS_KEY correct?")
    return bytes(a ^ b for a, b in zip(body, _stream(enc_key, nonce, len(body))))


class SubscriberStore:
    def __init__(self, path, secret, max_subscribers):
        self.path = path
        self.secret = secret
        _keys(secret)                 # fail early on a short key
        self.max_subscribers = max_subscribers
        self.offset = 0               # next Telegram update to read
        self.menu_version = 0         # which version of the command Menu Telegram has
        self.subs = {}                # chat_id (str) -> {"level": str, "cur": [..] or None}
        self.dirty = False

    @classmethod
    def load(cls, path=None, secret=None, max_subscribers=None):
        store = cls(path or cfg.SUBSCRIBERS_FILE, secret or cfg.SUBSCRIBERS_KEY,
                    max_subscribers or cfg.MAX_SUBSCRIBERS)
        if not os.path.exists(store.path):
            return store
        with open(store.path, "rb") as f:
            blob = f.read()
        try:
            data = json.loads(decrypt(store.secret, blob).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SubscriberError("Subscribers file is damaged.") from None
        store.offset = int(data.get("offset", 0))
        store.menu_version = int(data.get("menu", 0))
        for chat_id, rec in (data.get("subs") or {}).items():
            if isinstance(rec, dict):
                kept = {"level": rec.get("level", "high"), "cur": rec.get("cur")}
                for extra in ("exp", "xs"):          # expiry picks, expiry reminders already sent
                    if rec.get(extra):
                        kept[extra] = list(rec[extra])
                store.subs[str(chat_id)] = kept
        return store

    def save(self):
        if not self.dirty:
            return
        payload = json.dumps({"offset": self.offset, "menu": self.menu_version,
                              "subs": self.subs}, sort_keys=True)
        temp = self.path + ".tmp"
        with open(temp, "wb") as f:
            f.write(encrypt(self.secret, payload.encode("utf-8")))
        os.replace(temp, self.path)
        self.dirty = False

    def __len__(self):
        return len(self.subs)

    def get(self, chat_id):
        return self.subs.get(str(chat_id))

    def update(self, chat_id, level=None, currencies="keep", expiry="keep"):
        """Create or change a subscriber. `currencies`: a list, None (= all),
        or "keep". `expiry`: a list of instrument keys, None/[] (= none), or "keep".
        Returns False only when the subscriber limit blocks a signup."""
        chat_id = str(chat_id)
        rec = self.subs.get(chat_id)
        if rec is None:
            if len(self.subs) >= self.max_subscribers:
                return False
            rec = {"level": level or "high", "cur": None if currencies == "keep" else currencies}
        else:
            rec = dict(rec)
            if level is not None:
                rec["level"] = level
            if currencies != "keep":
                rec["cur"] = currencies
        if expiry != "keep":
            if expiry:
                rec["exp"] = list(expiry)
            else:
                rec.pop("exp", None)
        if self.subs.get(chat_id) != rec:
            self.subs[chat_id] = rec
            self.dirty = True
        return True

    def mark_expiry_sent(self, chat_id, token):
        rec = self.subs.get(str(chat_id))
        if rec is not None and token not in rec.get("xs", ()):
            self.subs[str(chat_id)] = dict(rec, xs=list(rec.get("xs", ())) + [token])
            self.dirty = True

    def prune_expiry_sent(self, oldest_date):
        """Forget reminder tokens ('key|YYYY-MM-DD|stage') for dates before oldest_date."""
        for chat_id, rec in list(self.subs.items()):
            old = rec.get("xs")
            if not old:
                continue
            keep = [t for t in old if t.split("|")[1] >= oldest_date]
            if keep != old:
                new = {k: v for k, v in rec.items() if k != "xs"}
                if keep:
                    new["xs"] = keep
                self.subs[chat_id] = new
                self.dirty = True

    def remove(self, chat_id):
        if self.subs.pop(str(chat_id), None) is not None:
            self.dirty = True
            return True
        return False

    def set_menu_version(self, version):
        if version != self.menu_version:
            self.menu_version = version
            self.dirty = True

    def set_offset(self, offset):
        if offset != self.offset:
            self.offset = offset
            self.dirty = True


class Audience:
    """Works out who should receive a given event.

    Owner chats (everything in TELEGRAM_CHAT_ID) get ALERT_MIN_IMPACT, all
    currencies, unless that chat changed its own settings with a command."""

    def __init__(self, owner_ids, store=None, default_level=None):
        self.owner_ids = [str(i) for i in (owner_ids or [])]
        self.store = store
        self._default_level = default_level

    @property
    def default_level(self):
        return self._default_level or cfg.ALERT_MIN_IMPACT

    def entries(self):
        """chat_id -> (level, currencies or None) for everyone receiving alerts."""
        out = {cid: (self.default_level, None) for cid in self.owner_ids}
        if self.store is not None:
            for cid, rec in self.store.subs.items():
                out[cid] = (rec.get("level"), rec.get("cur"))
        return {c: (lvl, cur) for c, (lvl, cur) in out.items() if lvl in cfg.IMPACT_RANK}

    def recipients_for(self, rank, currency=""):
        currency = (currency or "").upper()
        return [cid for cid, (lvl, cur) in self.entries().items()
                if rank >= cfg.IMPACT_RANK[lvl] and (not cur or currency in cur)]

    def expiry_picks(self):
        """chat_id -> instrument keys, for everyone who picked some and is not paused."""
        if self.store is None:
            return {}
        live = self.entries()
        return {cid: list(rec["exp"]) for cid, rec in self.store.subs.items()
                if rec.get("exp") and cid in live}

    def min_rank(self):
        """Lowest rank anyone wants; above the top rank if nobody is subscribed."""
        ranks = [cfg.IMPACT_RANK[lvl] for lvl, _ in self.entries().values()]
        return min(ranks) if ranks else len(cfg.IMPACT_RANK)

    def remove(self, chat_id):
        """Drop a subscriber who blocked the bot. Owner chats are never removed."""
        if self.store is not None and str(chat_id) not in self.owner_ids:
            self.store.remove(chat_id)


def build_audience(owner_ids):
    """The audience for this run. Without SUBSCRIBERS_KEY: owner chats only."""
    store = None
    if cfg.SUBSCRIBERS_KEY:
        try:
            store = SubscriberStore.load()
        except Exception as e:   # wrong key: stay owner-only, never overwrite the file
            print("SUBSCRIPTIONS DISABLED THIS RUN:", e)
    else:
        print("Subscriptions off (no SUBSCRIBERS_KEY): owner chats only.")
    return Audience(owner_ids, store)
