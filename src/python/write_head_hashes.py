#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Write the hash of the HEAD of the default branch for each repository to its own file.
If the file already exists, do nothing.
After this is done, the resulting files are used indefinitely, for reproducible results.
The default branch is often named "main" or "master".

usage: python3 write_head_hashes.py --repos_csv <repos.csv>
                                    --output_path <repos_head_passes.csv>

Input: a csv of repos.
The input file `repos.csv` must contain a header, one of whose columns is "repository".
That column contains a slug ("ORGANIZATION/REPO") for a GitHub repository.
Output: Write one file per repository, with the hash of the HEAD of the default branch
as column "head hash".
"""

import os
import sys
import argparse
from pathlib import Path
import multiprocessing
import pandas as pd
from repo import Repository
from test_repo_heads import num_processes
from loguru import logger
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TextColumn,
)


def get_latest_hash(args):
    """Collects the latest hash of the HEAD of the default branch for a repo.
    Args:
        Tuple[idx,row]: Information regarding that repo.
    Returns:
        pd.Series: repo information with the hash of the HEAD
    """
    _, row = args
    repo_slug: str = row["repository"]
    logger.info("write_head_hashes: " + repo_slug + " : Started get_latest_hash")

    try:
        logger.info("write_head_hashes " + repo_slug + " : Cloning repo")
        repo = Repository(
            "HEAD",
            repo_slug=repo_slug,
            workdir_id=repo_slug + "/head-" + repo_slug,
            lazy_clone=False,
        )
        row["head hash"] = repo.get_head_hash()
    except Exception as e:
        logger.info(  # type: ignore
            "write_head_hashes: "
            + repo_slug
            + " : Finished get_latest_hash, result = exception, cause: "
            + str(e)
        )
        return None

    logger.info("write_head_hashes: " + repo_slug + " : Finished get_latest_hash")
    return row


if __name__ == "__main__":
    Path("repos").mkdir(parents=True, exist_ok=True)

    logger.info("write_head_hashes: Started storing repo HEAD hashes")
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos_csv", type=Path)
    parser.add_argument("--output_path", type=Path)
    arguments = parser.parse_args()

    # If file exists ignore this step
    if os.path.isfile(arguments.output_path):
        logger.info(
            f"write_head_hashes: File already exists, skipping {arguments.output_path}"
        )
        sys.exit(0)

    df = pd.read_csv(arguments.repos_csv, index_col="idx")
    df["repository"] = df["repository"].str.lower()

    logger.info(
        "write_head_hashes: Started cloning repos and collecting head hashes for {arguments.output_path}"
    )
    with multiprocessing.Pool(processes=num_processes()) as pool:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task("Collecting hashes...", total=len(df))
            get_latest_hash_result = []
            for i in pool.imap_unordered(get_latest_hash, df.iterrows()):
                get_latest_hash_result.append(i)
                progress.update(task, advance=1)
    logger.info("write_head_hashes: Finished cloning repos and collecting head hashes")

    result_df = pd.DataFrame([i for i in get_latest_hash_result if i is not None])
    result_df.to_csv(arguments.output_path, index_label="idx")
    logger.info("write_head_hashes: Finished storing repo HEAD hashes")
