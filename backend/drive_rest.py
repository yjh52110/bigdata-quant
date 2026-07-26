"""Minimal Google Drive client over REST, for use inside Colab and Kaggle.

Why this exists rather than drive.mount():

  Measured on 2026-07, `drive.mount()` / `colab drivemount` fails headless with
  `ValueError: mount failed` because it needs the notebook's consent popup --
  and colabtools#4182 ("allow drive.mount() with Secrets") is still open, so
  there is no unattended FUSE path. Kaggle has no mount at all: google.colab is
  a Colab-only module and raises there. The REST API is the only route that
  works on both, and it answered in 33.8ms from a live Colab runtime.

Deliberately stdlib-only (urllib, no google-api-python-client, no requests) so
it runs in either runtime with nothing to pip install, and so it can be shipped
verbatim inside a Kaggle kernel's push folder.

Scope note: the platform authorises drive.file, which grants access only to
files this client itself created. ensure_folder() therefore finds only our own
folders, which is exactly the intended isolation -- it will never see, and can
never damage, the user's unrelated Drive contents.
"""

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

TOKEN_URI = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

# Drive rejects resumable chunks that aren't a multiple of 256 KiB.
CHUNK = 8 * 1024 * 1024
RETRIES = 5


class DriveError(RuntimeError):
    pass


# Transport-level hiccups that say nothing about the request. Seen in practice:
# a bare SSL EOF against oauth2.googleapis.com killed a dispatch outright. An
# unattended pipeline must not die on one of these.
_TRANSIENT = ("UNEXPECTED_EOF_WHILE_READING", "EOF occurred", "timed out",
              "Connection reset", "Temporary failure in name resolution",
              "Remote end closed")
NET_RETRIES = 3


def _request(url: str, *, method: str = "GET", data: Optional[bytes] = None,
             headers: Optional[Dict[str, str]] = None, timeout: int = 120):
    attempt = 0
    while True:
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return r.status, dict(r.headers), body
        except urllib.error.HTTPError as e:
            # An HTTP status is a real answer, including 401/403 -- hand it back
            # so callers can distinguish "Google said no" from "never reached
            # Google". Never retried: the answer would be the same.
            return e.code, dict(e.headers), e.read()
        except urllib.error.URLError as e:
            # Never reached Google: DNS, no egress, or an empty CA store. Call
            # out the certificate case because it otherwise reads as an outage.
            reason = str(getattr(e, "reason", e))
            if "CERTIFICATE_VERIFY_FAILED" in reason:
                raise DriveError(
                    f"could not reach {urllib.parse.urlsplit(url).netloc}: {reason}"
                    " -- this Python has no populated CA store; set SSL_CERT_FILE"
                    " to a CA bundle (Colab and Kaggle runtimes are fine)")
            attempt += 1
            if attempt > NET_RETRIES or not any(t in reason for t in _TRANSIENT):
                raise DriveError(
                    f"could not reach {urllib.parse.urlsplit(url).netloc}: {reason}"
                    + (f" (after {NET_RETRIES} retries)" if attempt > NET_RETRIES else ""))
            time.sleep(2 ** attempt)


def _json_request(url: str, **kw) -> Any:
    status, _, body = _request(url, **kw)
    text = body.decode(errors="ignore")
    if status >= 400:
        raise DriveError(f"HTTP {status}: {text[:400]}")
    return json.loads(text) if text else None


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchanges a refresh token for a short-lived access token."""
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    out = _json_request(TOKEN_URI, method="POST", data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    tok = out.get("access_token")
    if not tok:
        raise DriveError(f"no access_token in response: {str(out)[:200]}")
    return tok


def token_from_secret(raw: str) -> str:
    """Access token from the JSON blob stored in Colab/Kaggle Secrets.

    Expected shape: {"client_id": ..., "client_secret": ..., "refresh_token": ...}
    """
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise DriveError(f"secret is not valid JSON: {e}")
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not cfg.get(k)]
    if missing:
        raise DriveError(f"secret is missing {missing}")
    return access_token(cfg["client_id"], cfg["client_secret"], cfg["refresh_token"])


def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def about(token: str) -> Dict[str, Any]:
    return _json_request(f"{API}/about?fields=user,storageQuota", headers=_auth(token))


def find_folders(token: str, name: str, parent: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every folder with this name. Plural on purpose -- Drive allows duplicates.

    Verified 2026-07-26 against a live account: creating the same folder name
    twice under one parent yields two distinct ids, and a name query returns
    both. A filesystem would have refused the second.
    """
    # Escape single quotes: an unescaped name would break the query syntax.
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name='{safe}' and mimeType='{FOLDER_MIME}' and trashed=false"
    if parent:
        q += f" and '{parent}' in parents"
    url = (f"{API}/files?q={urllib.parse.quote(q)}"
           f"&fields=files(id,name,createdTime)&pageSize=100&orderBy=createdTime")
    out = _json_request(url, headers=_auth(token))
    return (out or {}).get("files") or []


def find_folder(token: str, name: str, parent: Optional[str] = None) -> Optional[str]:
    """One folder id, chosen deterministically when duplicates exist.

    Two writers racing on ensure_folder each see nothing and each create one, so
    duplicates do happen. Picking the lowest id means every writer converges on
    the same folder afterwards instead of splitting the dataset in half -- which
    is worse than the duplicate itself, because half the files become invisible
    to whoever holds the other id.
    """
    folders = find_folders(token, name, parent)
    if not folders:
        return None
    return min(f["id"] for f in folders)


def ensure_folder(token: str, name: str, parent: Optional[str] = None) -> str:
    """Find-or-create, then re-check.

    Drive has no conditional create, so this cannot be made atomic. What it can
    do is converge: after creating, look again and return the same deterministic
    winner every caller would pick. A loser's folder is left in place -- deleting
    it could race with a write already going into it -- and duplicate_folders()
    reports it for later cleanup.
    """
    existing = find_folder(token, name, parent)
    if existing:
        return existing
    meta: Dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        meta["parents"] = [parent]
    _json_request(f"{API}/files?fields=id", method="POST",
                  data=json.dumps(meta).encode(),
                  headers={**_auth(token), "Content-Type": "application/json"})
    # Deliberately ignore the id just created: another writer may have created
    # one too, and both must agree on which to use.
    winner = find_folder(token, name, parent)
    if winner is None:
        raise DriveError(f"created folder {name!r} but it is not visible")
    return winner


def duplicate_folders(token: str, path: str) -> List[Dict[str, Any]]:
    """Folders along a path that exist more than once, i.e. losers of a race."""
    dupes = []
    parent = None
    for part in [p for p in path.split("/") if p]:
        found = find_folders(token, part, parent)
        if len(found) > 1:
            keep = min(f["id"] for f in found)
            dupes.append({"name": part, "parent": parent, "keep": keep,
                          "extra": [f["id"] for f in found if f["id"] != keep]})
        if not found:
            break
        parent = min(f["id"] for f in found)
    return dupes


def ensure_path(token: str, path: str) -> str:
    """Creates a nested folder path like "chainquant/market/btcusdt_1m"."""
    parent = None
    for part in [p for p in path.split("/") if p]:
        parent = ensure_folder(token, part, parent)
    return parent or "root"


def upload(token: str, local_path: str, parent_id: str,
           name: Optional[str] = None) -> Dict[str, Any]:
    """Resumable upload of one file. Returns the created file's id and size.

    Resumable rather than simple upload because parquet parts here run to
    hundreds of MB and a single-shot PUT gives no way to recover a stalled
    transfer; on a 5xx or 429 the loop below re-queries the committed offset
    and continues from there instead of restarting.
    """
    name = name or os.path.basename(local_path)
    size = os.path.getsize(local_path)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"

    meta = json.dumps({"name": name, "parents": [parent_id]}).encode()
    status, headers, body = _request(
        f"{UPLOAD_API}/files?uploadType=resumable&fields=id,size",
        method="POST", data=meta,
        headers={**_auth(token), "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)})
    if status >= 400:
        raise DriveError(f"could not start upload: HTTP {status} {body.decode(errors='ignore')[:300]}")
    session = headers.get("Location") or headers.get("location")
    if not session:
        raise DriveError("resumable session URL missing from response headers")

    started = time.time()
    offset = 0
    attempts = 0
    with open(local_path, "rb") as f:
        while offset < size:
            f.seek(offset)
            chunk = f.read(CHUNK)
            end = offset + len(chunk) - 1
            st, hd, bd = _request(
                session, method="PUT", data=chunk,
                headers={"Content-Length": str(len(chunk)),
                         "Content-Range": f"bytes {offset}-{end}/{size}"},
                timeout=600)
            if st in (200, 201):
                elapsed = max(1e-6, time.time() - started)
                out = json.loads(bd.decode(errors="ignore") or "{}")
                return {"id": out.get("id"), "name": name, "bytes": size,
                        "seconds": round(elapsed, 2),
                        "mb_per_s": round(size / 1024 ** 2 / elapsed, 2)}
            if st == 308:
                # Drive reports the committed offset; trust it over our own count
                # so a partially-accepted chunk doesn't desync the stream.
                rng = hd.get("Range") or hd.get("range")
                offset = int(rng.split("-")[-1]) + 1 if rng else offset + len(chunk)
                attempts = 0
                continue
            if st in (429, 500, 502, 503, 504) and attempts < RETRIES:
                attempts += 1
                time.sleep(2 ** attempts)
                continue
            raise DriveError(f"chunk at {offset} failed: HTTP {st} {bd.decode(errors='ignore')[:300]}")
    raise DriveError("upload loop ended without a completion response")


def find_file(token: str, name: str, parent: Optional[str] = None) -> Optional[Dict[str, Any]]:
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    q = f"name='{safe}' and trashed=false"
    if parent:
        q += f" and '{parent}' in parents"
    url = f"{API}/files?q={urllib.parse.quote(q)}&fields=files(id,name,size)&pageSize=10"
    files = (_json_request(url, headers=_auth(token)) or {}).get("files") or []
    return files[0] if files else None


def download(token: str, file_id: str, dest_path: str,
             start: Optional[int] = None, end: Optional[int] = None) -> Dict[str, Any]:
    """Streams one file down, reporting throughput.

    Uses alt=media on the files endpoint; the drive.file scope covers reading
    back anything this client wrote. Pass start/end for a partial fetch -- but
    for analytics prefer duckdb_attach(), which lets the reader decide which
    ranges it needs instead of guessing here.
    """
    url = media_url(file_id)
    headers = dict(_auth(token))
    if start is not None:
        headers["Range"] = f"bytes={start}-{'' if end is None else end}"
    req = urllib.request.Request(url, headers=headers)
    started = time.time()
    total = 0
    try:
        with urllib.request.urlopen(req, timeout=600) as r, open(dest_path, "wb") as f:
            while True:
                chunk = r.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
    except urllib.error.HTTPError as e:
        raise DriveError(f"download failed: HTTP {e.code} {e.read().decode(errors='ignore')[:200]}")
    except urllib.error.URLError as e:
        raise DriveError(f"download failed: {getattr(e, 'reason', e)}")
    elapsed = max(1e-6, time.time() - started)
    return {"bytes": total, "seconds": round(elapsed, 2),
            "mb_per_s": round(total / 1024 ** 2 / elapsed, 2)}


def media_url(file_id: str) -> str:
    """The alt=media URL for a file, which is what a range-capable reader needs."""
    return f"{API}/files/{file_id}?alt=media"


def read_range(token: str, file_id: str, start: int, end: int) -> bytes:
    """Bytes [start, end] inclusive. Drive honours Range on alt=media.

    Verified 2026-07-26: a 1 MiB range against a 200 MB file returned HTTP 206
    with Content-Range bytes 0-1048575/209715200.
    """
    if start < 0 or end < start:
        raise DriveError(f"bad range {start}-{end}")
    status, _, body = _request(media_url(file_id),
                               headers={**_auth(token), "Range": f"bytes={start}-{end}"})
    if status not in (200, 206):
        raise DriveError(f"range read failed: HTTP {status} {body.decode(errors='ignore')[:200]}")
    return body


def duckdb_attach(con, token: str) -> None:
    """Lets DuckDB read Drive files in place, without downloading them first.

    httpfs issues ranged GETs, so a query pays for the columns and row groups it
    touches rather than the whole file. Measured on a 43 MB factor file over the
    same link: footer only 3.4s, one column 7.8-8.9s, two columns 12.2-12.6s,
    all eight 24.4-27.1s, whole-file download 22.0s. So narrow queries are worth
    roughly 3x, and only a genuinely all-column read costs more than downloading.

    The token expires in about an hour; re-run this to refresh it.
    """
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # A secret rather than a URL parameter: the token would otherwise end up in
    # DuckDB's query log and in any error message quoting the SQL.
    con.execute("CREATE OR REPLACE SECRET gdrive (TYPE http, "
                f"EXTRA_HTTP_HEADERS MAP {{'Authorization': 'Bearer {token}'}});")


def upload_tree(token: str, local_dir: str, drive_path: str) -> Dict[str, Any]:
    """Mirrors a local directory into Drive, preserving relative structure."""
    root_id = ensure_path(token, drive_path)
    folder_ids: Dict[str, str] = {"": root_id}
    uploaded: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    started = time.time()

    for dirpath, _, filenames in os.walk(local_dir):
        rel = os.path.relpath(dirpath, local_dir)
        rel = "" if rel == "." else rel
        if rel and rel not in folder_ids:
            parent = folder_ids.get(os.path.dirname(rel), root_id)
            folder_ids[rel] = ensure_folder(token, os.path.basename(rel), parent)
        for fn in sorted(filenames):
            try:
                uploaded.append(upload(token, os.path.join(dirpath, fn), folder_ids[rel]))
            except DriveError as e:
                # One bad file must not abandon the rest of the batch.
                failed.append({"file": os.path.join(rel, fn), "error": str(e)[:300]})

    total = sum(u["bytes"] for u in uploaded)
    elapsed = max(1e-6, time.time() - started)
    return {"drive_path": drive_path, "folder_id": root_id,
            "files": len(uploaded), "bytes": total,
            "seconds": round(elapsed, 2),
            "mb_per_s": round(total / 1024 ** 2 / elapsed, 2),
            "failed": failed}
