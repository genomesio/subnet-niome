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
        self.task_id = task.id
        logger.info(f"Fetched task {task.id}")

        self.validated_uids = []
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
            neuron = self.metagraph.neurons[uid]
            axon_endpoint = neuron.axon
            if axon_endpoint is None:
                continue

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
        task = fetch_task(self)
        self.task_id = task.id
        logger.info(f"Fetched task {task.id}")

        miner_uids = get_miner_uids(self)
        scores = []

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
            region_name=config.AWS_REGION,
        )

        for uid in miner_uids:
            if uid in self.validated_uids:
                continue

            self.validated_uids.append(uid)

            try:
                s3_client.download_file(
                    config.AWS_S3_BUCKET,
                    f"niome/{uid}.json",
                    config.MINER_SUBMISSION_PATH,
                )
                miner_score = benchmark_submission(uid)
                scores.append(miner_score)
                self.save_state()

                s3_client.upload_file(
                    Filename=config.MINER_SUBMISSION_PATH,
                    Bucket=config.AWS_S3_BUCKET,
                    Key=f"niome/{uid}.json",
                )
            except Exception:
                continue

        logger.info(f"Scores: {scores}")

        self.set_weights(scores, self.task_id)
        owner_uid = self.metagraph.hotkeys.index(config.OWNER_HOTKEY)
        uids_without_owner = [uid for uid in self.uids if uid != owner_uid]
        if len(uids_without_owner) > 0:
            upload_final_submissions_to_server(self, uids_without_owner)
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
            self.subtensor.execute(
                bt.SetWeights(
                    netuid=self.config.netuid,
                    uids=self.uids,
                    weights=self.weights,
                ),
                self.wallet,
            )
        else:
            blocks = (self.block - config.BASE_BLOCK_NUMBER) % config.INTERVAL_BLOCKS

            if blocks < config.VALIDATION_BLOCK and not self.is_broadcasting:
                self.is_validating = False
                self.is_broadcasting = True
                asyncio.create_task(broadcast_task(self))
            if blocks >= config.VALIDATION_BLOCK and not self.is_validating:
                self.is_validating = True
                self.is_broadcasting = False
                self.are_weights_committed = False
                asyncio.create_task(run_validation(self))
    except Exception as e:
        logger.error(f"Error during forward step: {e}")

    time.sleep(5)
