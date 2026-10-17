## Validator Setup Guide

To contribute as a Validator in the NIOME(SN55), you must prepare your machine, install the required packages, and register your identity on Bittensor. Validators play a critical role in evaluating miner outputs and maintaining subnet integrity.

### 1. Prerequisites

Before starting, ensure your system meets the requirements and has the core dependencies installed.

* **Operating System:** Ubuntu 22.04 or similar Linux distribution is generally recommended for optimal compatibility. Mining is not supported on Windows.
* **Python:** Python 3.12
* **Git:** required for cloning the repository
* **Hardware:** 
   - vCPU :  32
   - GPU : unnecessary
   - Memory : 64GB recommended
   - Storage : 1TB SSD recommended
   - 3rd Party API : unnecessary
   - Port Forwarding : standard

Validaors must maintain high uptime and stable networking, as they responsible for scoring miners and producing consensus signals

### 2. Environment Setup

This section walks you through cloning the subnet-niome repository and installing the required packages.

1. **Clone the Repository:**
   **Bash**

   ```
   git clone https://github.com/genomesio/subnet-niome.git
   cd subnet-niome
   ```
2. **Create a Virtual Environment (Recommended):**
   **Bash**

   ```
   python3 -m venv venv
   source venv/bin/activate
   ```
3. **Install Dependencies:** Install the required Python packages and register the local package for execution.
   **Bash**

   ```
   python3 -m pip install -r requirements.txt
   ```
4. **Install Docker:** Install the Docker for PharmCAT
   **Bash**

   ***Set up the repository***
   ```
   sudo apt-get update 
   sudo apt-get install -y ca-certificates curl gnupg
   sudo install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
   sudo chmod a+r /etc/apt/keyrings/docker.gpg 
   ```

   ***Add Docker's APT source***
   ```
   echo \ 
   "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \ 
   $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
   sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
   ```
   
   ***Install Docker Packages***
   ```
   sudo apt-get update 
   sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
   ```

   ***Verify it works***
   ```
   docker run --rm hello-world
   ```

5. **Configure Storage:** Miners upload their submissions to storage the validator provides. Copy `.env.example` to `.env` and fill in **one** of:

   - **AWS S3:** `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_S3_BUCKET`. S3 is used whenever these are set.
   - **Google Drive:** used when the AWS values are empty.
     1. In Google Cloud, enable the Google Drive API and create a service account with a JSON key.
     2. Create a folder in a **Shared Drive** and add the service account's `client_email` as a Content manager. (Service accounts have no storage of their own, so uploads into a regular "My Drive" folder fail.)
     3. Set `GOOGLE_SERVICE_ACCOUNT_FILE` to the key file path and `GOOGLE_DRIVE_ROOT_FOLDER_ID` to the folder ID (the last part of the folder's URL).

### 3. Running the Validator

Once your hotkey is registered, you can start your Validator.

1. **Make the script executable (first time only):**

   ```bash
   chmod +x entrypoint.sh
   ```

2. **Run interactively:**

   ```bash
   ./entrypoint.sh
   ```

   `--wandb.api_key` is optional — omit it if you are not using Weights & Biases.

   The script will:
   - Create and activate a Python virtual environment (`.venv`) if one does not exist
   - Install all Python dependencies from `requirements.txt`
   - Install system tools (`bwa`, `samtools`, `tabix`, `bcftools`)
   - Start `neurons/validator.py` as the PM2 process `niome_validator`
   - Check the `main` branch every 60 seconds and automatically pull + restart on new commits

   You will be prompted for `wallet.name`, `wallet.hotkey`, and optionally `wandb.api_key`.
