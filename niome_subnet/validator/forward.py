# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# Copyright © 2025 Genomes.io

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import asyncio
import logging
import boto3
import bittensor as bt
import httpx
import niome_subnet.utils.settings as config
import numpy as np
import os
import time

from niome_subnet.api import (
    fetch_cell_types,
    fetch_task,
    upload_final_submissions_to_server,
)
from niome_subnet.genomics.validation import benchmark_submission
from niome_subnet.protocol import GenomicsTaskSynapse
from niome_subnet.utils import get_miner_uids

logger = logging.getLogger(__name__)


async def query_miner(self, uid: int, axon_endpoint: str, synapse: GenomicsTaskSynapse):
    """Send task to a single miner via HTTP and return the response."""
    try:
        url = f"http://{axon_endpoint}/forward"
        body = synapse.model_dump_json().encode()
        headers = bt.http_auth.sign(
            self.wallet,
            method="POST",
            path="/forward",
            body=body,
        )
        headers["Content-Type"] = "application/json"
        async with httpx.AsyncClient(timeout=config.FORWARD_TIMEOUT) as client:
            resp = await client.post(url, content=body, headers=headers)
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"Error querying miner {uid} at {axon_endpoint}: {e}")
        return None

async def broadcast_task(self):
    logger.info("Broadcasting task ...")
    try:
        os.makedirs("data", exist_ok=True)

        task = fetch_task(self)
        logger.info(f"Fetched task {task.id}")

        if self.task_id != task.id:
            self.collected_uids = []
            self.task_id = task.id

        self.save_state()

        miner_uids = get_miner_uids(self)
        np.random.shuffle(miner_uids)

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION,
        )

        for uid in miner_uids:
            if uid in self.collected_uids:
                continue

            neuron = self.metagraph.neurons[uid]
            axon_endpoint = neuron.axon
            if axon_endpoint is None:
                continue

            # One submission per machine and per owner: a coldkey running many
            # hotkeys behind one IP must not get more shots at the task.
            ip = str(axon_endpoint).rsplit(":", 1)[0]
            coldkey = neuron.coldkey

            self.collected_uids.append(uid)
            self.save_state()

            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={"Bucket": config.AWS_S3_BUCKET, "Key": f"niome/{uid}.json"},
                ExpiresIn=config.SUBMISSION_TIMEOUT,
            )

            synapse = GenomicsTaskSynapse(
                task=task,
                presigned_url=presigned_url,
                timeout=config.FORWARD_TIMEOUT,
            )
            await query_miner(self, uid, axon_endpoint, synapse)
    except Exception as e:
        logger.error(f"Error during broadcasting: {e}")

async def run_validation(self):
    logger.info("Validating miners' submissions ...")
    try:
        os.makedirs("data", exist_ok=True)

        fetch_task(self)
        cell_types = fetch_cell_types(self)

        miner_uids = get_miner_uids(self)
        scores = []

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION,
        )

        for uid in miner_uids:
            try:
                s3_client.download_file(
                    config.AWS_S3_BUCKET,
                    f"niome/{uid}.json",
                    config.MINER_SUBMISSION_PATH,
                )
                miner_score = benchmark_submission(cell_types, uid)
                scores.append(miner_score)
                self.save_state()

                s3_client.upload_file(
                    Filename=config.MINER_SUBMISSION_PATH,
                    Bucket=config.AWS_S3_BUCKET,
                    Key=f"niome/{uid}.json",
                )
            except Exception:
                continue

        valid_scores = [score for score in scores if score.final_score > 0]
        logger.info(f"Scores: {valid_scores}")

        self.set_weights(scores, self.task_id)
        top_uids = [
            s.uid for s in sorted(scores, key=lambda s: s.final_score, reverse=True)[:config.FINAL_SUBMISSION_COUNT]
        ]
        if len(top_uids) > 0:
            upload_final_submissions_to_server(self, top_uids)
        logger.info("Finished validation.")
    except Exception as e:
        logger.error(f"Error during validation: {e}")

async def forward(self):
    """
    The forward function is called by the validator every time step.

    It is responsible for querying the network and scoring the responses.

    Args:
        self (:obj:`bittensor.neuron.Neuron`): The neuron object which contains all the necessary state for the validator.

    """
    try:
        if config.BURNING_RATE == 1.0 and not self.are_weights_committed:
            self.are_weights_committed = True
            self.set_weights([], "")
            weights_dict = {int(uid): float(w) for uid, w in zip(self.uids, self.weights)}
            bt.set_weights(
                self.config.netuid,
                weights_dict,
                wallet=self.wallet,
                version_key=self.spec_version,
            )
        else:
            blocks = (self.block - config.BASE_BLOCK_NUMBER) % config.INTERVAL_BLOCKS

            if blocks < config.VALIDATION_BLOCK and not self.is_broadcasting:
                self.is_validating = False
                self.is_broadcasting = True
                asyncio.create_task(broadcast_task(self))
            if blocks >= config.VALIDATION_BLOCK and blocks < config.WEIGHT_SET_BLOCK and not self.is_validating:
                self.is_validating = True
                self.is_broadcasting = False
                self.are_weights_committed = False
                asyncio.create_task(run_validation(self))
    except Exception as e:
        logger.error(f"Error during forward step: {e}")

    time.sleep(5)
