import logging

import json
import niome_subnet.utils.settings as config
import requests
import time
import urllib.request

from niome_subnet.genomics.model import MinerScore, MinerScoreDto, Task

logger = logging.getLogger(__name__)


def get(self, url: str):
    timestamp = str(time.time())
    canonical = json.dumps({
        'payload': json.dumps({}, separators=(',', ':'), sort_keys=True),
        'hotkey': self.wallet.hotkey.ss58_address,
        'netuid': str(self.netuid),
        'timestamp': timestamp,
    }, separators=(',', ':'), sort_keys=True)

    signature = self.wallet.hotkey.sign(canonical.encode()).hex()

    for attempt in range(1, config.MAX_TASK_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=self.build_signature_headers(
                    signature=signature,
                    hotkey=self.wallet.hotkey.ss58_address,
                    timestamp=timestamp,
                    netuid=str(self.netuid),
                ),
                timeout=config.TASK_REQUEST_TIMEOUT,
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"Backend returned status {response.status_code}"
                )

            return response.json()
        except Exception as e:
            logger.error(f"Get Error: {str(e)}")
            if attempt < config.MAX_TASK_RETRIES:
                delay = config.BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                logger.error("All retries failed")
                raise e

def post(self, url: str, payload: dict):
    timestamp = str(time.time())
    canonical = json.dumps({
        'payload': json.dumps(payload, separators=(',', ':'), sort_keys=True),
        'hotkey': self.wallet.hotkey.ss58_address,
        'netuid': str(self.netuid),
        'timestamp': timestamp,
    }, separators=(',', ':'), sort_keys=True)

    signature = self.wallet.hotkey.sign(canonical.encode()).hex()

    for attempt in range(1, config.MAX_TASK_RETRIES + 1):
        try:
            response = requests.post(
                url,
                headers=self.build_signature_headers(
                    signature=signature,
                    hotkey=self.wallet.hotkey.ss58_address,
                    timestamp=timestamp,
                    netuid=str(self.netuid),
                ),
                json=payload,
                timeout=config.TASK_REQUEST_TIMEOUT,
            )

            if response.status_code != 200:
                raise RuntimeError(
                    f"Backend returned status {response.status_code}"
                )

            return response.json()
        except Exception as e:
            logger.error(f"Post Error: {e}")
            if attempt < config.MAX_TASK_RETRIES:
                delay = config.BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                logger.error("All retries failed")
                raise e

def fetch_task(self) -> Task:
    """Generate a synthetic genomic simulation task with retry logic and fallback."""
    data = get(self, config.TASK_URL)

    if "task_id" in data:
        data["id"] = data.pop("task_id")

    task = Task.model_validate(data)

    if not task.contract_url or not task.hbb_ref_url:
        logger.error("Invalid response: missing contract_url or hbb_ref_url")
        raise RuntimeError("Invalid response")

    download_file(task.contract_url, config.CONTRACT_PATH)
    download_file(task.hbb_ref_url, config.HBB_REFERENCE_PATH)

    return task

def download_file(url: str, path: str) -> None:
    """Download `url` to `path`, retrying transient storage errors (e.g. Drive 5xx)."""
    for attempt in range(1, config.MAX_TASK_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=config.TASK_REQUEST_TIMEOUT) as response:
                data = response.read()
            with open(path, "wb") as f:
                f.write(data)
            return
        except Exception as e:
            logger.error(f"Download of {path} failed (attempt {attempt}/{config.MAX_TASK_RETRIES}): {e}")
            if attempt < config.MAX_TASK_RETRIES:
                time.sleep(config.BASE_DELAY_SECONDS * (2 ** (attempt - 1)))
            else:
                raise

def fetch_cell_types(self) -> dict:
    return get(self, config.CELL_TYPES_URL)

def submit_validation_result(self, miner_scores: list[MinerScoreDto]) -> None:
    """Submit miner scores with retry logic and fallback."""
    payload = {
      "scores": [score.model_dump() for score in miner_scores],
    }
    post(self, config.MINER_SCORE_URL, payload)

def upload_final_submissions_to_server(self, scores: list[MinerScore], submissions: dict[int, list]) -> None:
    """Upload the scored submissions to the server, best first.

    The uploaded file is a JSON list ordered by final_score (highest first, ties broken
    by lower uid), so its first entry is always the task's best submission.
    `submissions` maps uid to the exact submission that uid was scored on.
    """
    try:
        ranked = sorted(scores, key=lambda s: (-s.final_score, s.uid))
        final_submissions = []

        for rank, score in enumerate(ranked, start=1):
            submission = submissions.get(score.uid)
            if not submission:
                logger.error(f"No scored submission kept for UID {score.uid}, skipping")
                continue
            final_submissions.append({
                "rank": rank,
                "uid": int(score.uid),
                "final_score": float(score.final_score),
                "submission": submission,
            })

        if not final_submissions:
            logger.warning("No final submissions to upload")
            return

        data = get(self, f"{config.MINER_SUBMISSION_URL}?task_id={self.task_id}")
        presigned_url = data["url"]

        response = requests.put(
            presigned_url,
            data=json.dumps(final_submissions),
            headers={
                "Content-Type": "application/json"
            },
            timeout=300,
        )

        response.raise_for_status()

        logger.info(f"Successfully uploaded {len(final_submissions)} final submissions")
    except Exception as e:
        logger.error(f"Error in upload_final_submissions_to_server: {e}")
        raise e
