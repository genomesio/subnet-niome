# NIOME : Bittensor Subnet(SN55) for Decentralized Synthetic CRISPR Dataset Generation

Welcome to the NIOME Subnet! This repository contains all the necessary information to get started, understand our subnet architecture, and contribute.

![niome logo image](docs/logo.png)

## Overview

**NIOME** is a decentralized compute network for the generation of **synthetic CRISPR experiment datasets** focused on the **HBB (beta-globin) gene locus**, including clinically relevant mutations such as HbS. Built on the Bittensor network, NIOME coordinates miners and validators to generate **biologically structured, experimentally plausible CRISPR datasets** under progressively evolving validation constraints, enabling scalable genomic editing research without the cost and limitations of experimental data collection.

## Purpose

A fundamental limitation in genome editing research is the scarcity of large-scale, diverse, and structured CRISPR datasets. Existing datasets are:

- Expensive to generate experimentally
- Biased toward a small number of loci
- Limited in mutation diversity (especially in HBB-related disease variants)
- Insufficient for training robust generalizable models

The NIOME subnet addresses this by creating a **decentralized synthetic CRISPR data generation system constrained by biological validation rules**. This enables:

- Scalable exploration of CRISPR sequence-function space
- Expanded mutation coverage across HBB disease variants
- Structurally consistent synthetic experimental datasets
- Improved downstream ML model training data

The goal is not prediction, but **high-fidelity synthetic biological data generation at scale**.

## System Flow

1. **Task Generation (Backend → Validators)** The NIOME backend generates CRISPR dataset generation tasks focused on the HBB gene locus. These tasks include contract specifications defining allowed mutations, cell types, and structural constraints. Validators fetch these tasks directly from the backend.

2. **Task Distribution (Validators → Miners)** After receiving a task, validators broadcast it to miners on the subnet. Each miner receives the same challenge, ensuring fair and comparable evaluation across participants.

3. **CRISPR Dataset Generation (Miners → Validators)** Miners generate lists of CRISPR experiments conforming to the task specification. Each experiment includes guide RNA sequences, target positions, mutation types, and Cas system choices. Miners upload their datasets to validator-provided storage (an S3 bucket or a Google Drive folder) through an upload URL the validator sends them.

4. **Multi-Stage Validation (Validators)** Validators evaluate miner submissions through a progressive validation pipeline:
   - **Stage 1:** Structural biological gate (syntactic correctness)
   - **Stage 2:** Statistical plausibility (biological realism)
   - **Stage 3:** Biophysical outcome simulation (cut efficiency, repair pathways)
   - **Stage 4:** Cross-consistency learning evaluation
   - **Stage 5:** Distributional fidelity to real CRISPR data

5. **Scoring and Rewards (Validators → Network)** Based on multi-stage evaluation, validators assign scores that determine miner emissions in $TAO.

## Core Features

**Biologically Constrained Data Generation:** NIOME generates synthetic CRISPR datasets that are structurally valid, biologically plausible, and statistically consistent with known genomic editing principles.

**Progressive Validation Architecture:** Validation increases in biological complexity over time, from basic structural checks to sophisticated biophysical simulations and distributional fidelity tests.

**Anti-Cheating Design:** Miners submit only experimental designs (guide sequences, targets, mutations). All biological outcomes (efficiency, repair pathways, indel patterns) are computed exclusively by validators, preventing optimization shortcuts.

**HBB Disease Focus:** Specialized focus on the HBB gene locus enables deep exploration of mutations relevant to sickle cell disease, beta-thalassemia, and other hemoglobinopathies.

**Scalable Dataset Production:** Synthetic CRISPR datasets can be generated at arbitrary scale, enabling large-scale training data for genome editing ML models without experimental costs.

## Core Components

### Miners

Miners generate lists of CRISPR experiments conforming to task specifications. Each experiment must include:

**Required Fields:**

- `experiment_id` - Unique identifier
- `guideRNA` - CRISPR guide sequence (20-23bp)
- `target_alignment_start` and `target_alignment_end` - Genomic coordinates
- `strand` - DNA strand (`+` or `-`)
- `mutation` - HBB-associated variant from allowed set
- `cas_system` - `Cas9` or `Cas12a`
- `cell_type` - From approved whitelist

**Strict Prohibitions:**
Miners must NOT include predicted efficiency scores, repair outcomes, off-target risks, or any derived biological labels. All outcomes are computed by validators.

**Incentives:**
Miners are rewarded in $TAO based on structural validity, biological plausibility, outcome realism, cross-consistency, and distributional fidelity of their generated datasets.

### Validators

Validators run a multi-stage validation pipeline:

1. **Stage 1 - Structural Gate:** Validates DNA syntax, PAM sequences, guide length, mutation whitelist compliance
2. **Stage 2 - Statistical Plausibility:** Evaluates GC content, spatial consistency, structural realism
3. **Stage 3 - Biophysical Simulation:** Simulates cut probability, repair pathways (HDR/NHEJ), indel distributions
4. **Stage 4 - Cross-Consistency:** Checks learned biological relationships using ML models
5. **Stage 5 - Distributional Fidelity:** Compares synthetic datasets to real CRISPR data distributions

### Research Integration

We systematically update validation models in response to emerging academic research:

- Integration of Azimuth / Rule Set 3 for efficiency prediction
- inDelphi / FORECasT for repair outcome modeling
- CFD-like models for off-target assessment

## Guide for Miners and Validators

- [Miner Setup](docs/miner_guide.md)
- [Validator Setup](docs/validator_guide.md)

## Community

For real-time discussions, community support, and regular updates, <a href="https://discord.com/invite/bittensor">join the bittensor discord</a>. Connect with developers, researchers, and users to get the most out of the NIOME Subnet.

## License

This repository is licensed under the MIT License.

```text
# The MIT License (MIT)
# Copyright © 2024 Opentensor Foundation

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
```
