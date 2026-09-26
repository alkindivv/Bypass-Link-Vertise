#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Linkvertise bypass — self-contained (query di-embed, cuma butuh `requests`).

Ambil link tujuan asli dari link linkvertise/link-target TANPA nonton iklan.

Cara kerja (3 langkah GraphQL ke publisher.linkvertise.com):
  1. query getContent    -> daftar task: AdTask/WaitTask status IN_PROGRESS (+ PremiumTask, abaikan)
  2. mutation completeTask per task -> server balas DONE (server TIDAK cek iklan benar-benar ditonton,
     cukup kirim action_id uuid unik tiap panggilan)
  3. query getContent lagi -> begitu semua AdTask DONE, respons berubah jadi
     DetailPageTargetData { type, url, paste } = LINK TUJUAN ASLI

Pakai:
  python3 main.py "https://link-target.net/462274/SsKGTobQOnlH"
  python3 main.py "https://linkvertise.com/462274/SsKGTobQOnlH"
  # atau tanpa argumen: mode interaktif, tempel link satu per satu

Bisa banyak link sekaligus:
  python3 main.py "link1" "link2" "link3"

Output: JSON {"type","url","paste"} + baris "TARGET: <url>" siap copy.
Exit code 0 = sukses, 1 = gagal.
"""
import json
import re
import sys
import time
import uuid

try:
    import requests
except ImportError:
    sys.exit("Butuh modul requests: pip install requests")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
GQL = "https://publisher.linkvertise.com/graphql"
MAX_ROUNDS = 12          # tiap round = 1 babak iklan; 3-5 round biasanya cukup

Q_GETCONTENT = """query getContent($identifier: PublicLinkIdentificationInput!, $task_args: TaskArgument) {
  getContent(input: $identifier, task_args: $task_args) {
    ... on ContentAccessTaskSet {
      __typename
      tasks {
        __typename
        id
        ... on PremiumTask {
          __typename
          status
          id
        }
        ... on WaitTask {
          __typename
          id
          remainingWaitingTime
          status
          adsTotal
        }
        ... on AdTask {
          __typename
          id
          status
          payloadBag {
            taboola {
              session_id
            }
          }
          adIndex
          adsTotal
        }
      }
    }
    ... on DetailPageTargetData {
      type
      url
      paste
    }
  }
}"""

Q_COMPLETETASK = """mutation completeTask($identifier: PublicLinkIdentificationInput!, $task_id: String!, $task_args: TaskArgument) {
  completeTask(input: $identifier, task_id: $task_id, task_args: $task_args) {
    id
    ... on AdTask {
      __typename
      id
      status
    }
    ... on PremiumTask {
      __typename
      status
      id
    }
    ... on WaitTask {
      __typename
      id
      status
      remainingWaitingTime
      adsTotal
    }
  }
}"""


def parse_link(url):
    """Ambil (publisher_id, slug) dari berbagai bentuk domain linkvertise."""
    url = url.strip()
    m = re.search(r'(?:link-target|linkvertise|link-center|link-hub|linkpays|direct-link|link-to)\.'
                  r'(?:net|com)/(\d+)/([\w-]+)', url)
    if not m:
        m = re.search(r'linkvertise\.com/(\d+)/([\w-]+)', url)
    if not m:
        raise ValueError("format link tidak dikenali (butuh .../<publisher_id>/<slug>)")
    return m.group(1), m.group(2)


def bypass(link, verbose=True):
    uid, slug = parse_link(link)
    ident = {"userIdAndUrl": {"url": slug, "user_id": uid}}
    page = f"https://linkvertise.com/access/{uid}/{slug}"

    def task_args():
        # action_id harus uuid baru tiap panggilan; additional_data cuma telemetry taboola
        return {"additional_data": {"taboola": {"user_id": "fallbackUserId", "consent_string": "",
                 "url": page, "external_referrer": "", "session_id": None}},
                "action_id": str(uuid.uuid4())}

    s = requests.Session()
    s.headers.update({"User-Agent": UA,
                      "Accept": "application/json, text/plain, */*",
                      "Content-Type": "application/json",
                      "Origin": "https://linkvertise.com",
                      "Referer": page})
    s.get(page, timeout=40)          # sekadar ambil cookie __cf_bm; tanpa ini tetap jalan

    def gql(op, variables, query):
        r = s.post(f"{GQL}?name={op}",
                   json={"operationName": op, "variables": variables, "query": query}, timeout=40)
        try:
            return r.json()
        except Exception:
            return {"raw": r.text[:300], "http": r.status_code}

    for rnd in range(MAX_ROUNDS):
        j = gql("getContent", {"identifier": ident, "task_args": task_args()}, Q_GETCONTENT)
        node = (j.get("data") or {}).get("getContent") or {}
        if verbose:
            label = node.get("__typename") or ("DetailPageTargetData" if node.get("url") else "?")
            print(f"[round {rnd}] {label}: {json.dumps(node)[:180]}", flush=True)

        if node.get("url"):                      # sudah kebuka
            return node

        for t in node.get("tasks", []):
            if t.get("status") == "DONE" or t.get("__typename") == "PremiumTask":
                continue                          # PremiumTask selalu OPEN -> jangan disentuh
            j2 = gql("completeTask",
                     {"identifier": ident, "task_id": t["id"], "task_args": task_args()},
                     Q_COMPLETETASK)
            st = ((j2.get("data") or {}).get("completeTask") or {}).get("status")
            if verbose:
                print(f"   completeTask {t.get('__typename')} #{t.get('adIndex', '-')} -> {st}", flush=True)
            time.sleep(0.7)
        time.sleep(0.8)

    return None


def main():
    args = sys.argv[1:]
    if not args:                                   # mode interaktif
        print("Tempel link linkvertise/link-target (Enter kosong = keluar):")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                break
            args.append(line)

    ok = 0
    for link in args:
        print(f"\n=== {link} ===")
        try:
            res = bypass(link)
        except Exception as e:
            print(f"  GAGAL: {type(e).__name__}: {e}")
            continue
        if res:
            print("\n=== TARGET ===")
            print(json.dumps(res, indent=1, ensure_ascii=False))
            print(f"TARGET: {res.get('url')}")
            ok += 1
        else:
            print("  GAGAL: target tidak kebuka (skema linkvertise mungkin berubah)")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
