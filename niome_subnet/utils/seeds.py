import hashlib
import logging
import niome_subnet.utils.settings as config
import time

from niome_subnet.utils.misc import FINALITY_LAG

logger = logging.getLogger(__name__)


def seed_blocks(block: int) -> list[int]:
    """The SEED_COUNT blocks whose hashes seed the round that `block` falls in.

    The window opens at SEED_BLOCK and closes before VALIDATION_BLOCK, so every validator
    reads the same heights whenever in the validation window it happens to start.
    """
    round_start = block - (block - config.BASE_BLOCK_NUMBER) % config.INTERVAL_BLOCKS
    start = round_start + config.SEED_BLOCK
    return [start + offset for offset in range(config.SEED_COUNT)]


def _block_hash(subtensor, block: int) -> str:
    """The hash of `block`, retrying the transient errors a lagging node throws."""
    delay = 5.0
    for attempt in range(1, config.SEED_READ_ATTEMPTS + 1):
        try:
            info = subtensor.block_info(block)
            if info is None or not info.hash:
                raise RuntimeError(f"node served no header for block {block}")
            return info.hash
        except Exception as e:
            if attempt == config.SEED_READ_ATTEMPTS:
                raise
            logger.warning(
                f"Could not read the hash of block {block} "
                f"(attempt {attempt}/{config.SEED_READ_ATTEMPTS}): {e}. Retrying in {delay:.1f}s"
            )
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


def generate_seeds(current_block, subtensor) -> list[int]:
    """This round's benchmark seeds: one per block hash, read straight from the chain.

    Validators derive these themselves instead of reading the task's seed, so the benchmark
    is not something the task can choose and not something a miner can see while there is
    still time to overfit to it.
    """
    blocks = seed_blocks(current_block)

    # Inert with the current SEED_BLOCK: the window is buried ~18 blocks deep before
    # validation opens. It only fires if that gap is ever narrowed to less than
    # FINALITY_LAG, where a reorg could still rewrite a hash and leave two validators
    # seeding off different values -- a split that shows up as divergent weights, never as
    # an error, so it is worth keeping the guard cheap and automatic.
    target = blocks[-1] + FINALITY_LAG
    if current_block < target:
        logger.info(f"Waiting for block {target} before reading seed blocks {blocks}")
        try:
            subtensor.wait_for_block(target, timeout=config.SEED_FINALITY_TIMEOUT)
        except Exception as e:
            logger.warning(f"Gave up waiting for block {target} ({e}); reading anyway")

    hashes = [_block_hash(subtensor, block) for block in blocks]
    seeds = seeds_from_block_hashes(hashes)
    for block, block_hash, seed in zip(blocks, hashes, seeds):
        logger.info(f"Seed {seed} from block {block} ({block_hash})")
    return seeds


def _hash_bytes(block_hash):
    """Validate a 0x-hex (or bare hex) block hash and return its raw bytes."""
    raw = bytes.fromhex(str(block_hash).removeprefix("0x"))
    if len(raw) != config.BLOCK_HASH_BYTES:
        raise ValueError(
            f"expected a {config.BLOCK_HASH_BYTES}-byte block hash, got {block_hash!r}"
        )
    return raw


def seed_from_block_hash(block_hash, exclude=(), seed_range=config.SEED_RANGE):
    """One seed from one block hash, skipping any value already in `exclude`.

        seed = low + sha256(block_hash_bytes || counter_u32_be) % span

    starting at counter 0 and advancing while the value is excluded. Pure and
    deterministic: two validators reading the same block cannot disagree, and the rule is
    short enough for a miner to reimplement and audit.
    """
    raw = _hash_bytes(block_hash)
    low, high = seed_range
    span = high - low + 1
    if span <= len(set(exclude)):
        raise ValueError(
            f"seed range {seed_range} is exhausted by {len(set(exclude))} excluded seeds"
        )

    counter = 0
    while True:
        digest = hashlib.sha256(raw + counter.to_bytes(4, "big")).digest()
        seed = low + int.from_bytes(digest, "big") % span
        counter += 1
        if seed not in exclude:
            return seed


def seeds_from_block_hashes(block_hashes, seed_range=config.SEED_RANGE):
    """One seed per block hash, in the order given, all distinct.

    Distinct because equal seeds would collapse the round into fewer benchmark runs than
    intended; the tie-break walks the counter on the later hash, which is deterministic, so
    it cannot become a source of validator disagreement.
    """
    seeds = []
    for block_hash in block_hashes:
        seeds.append(seed_from_block_hash(block_hash, exclude=seeds, seed_range=seed_range))
    return seeds
