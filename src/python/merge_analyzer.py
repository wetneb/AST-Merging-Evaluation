#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analyze the merges i.e. check if the parents pass tests and statistics between merges.
usage: python3 merge_analyzer.py --repos_head_passes_csv <path_to_repos_head_passes.csv>
                                --merges_path <path_to_merges>
                                --output_dir <output_dir>
                                --cache_dir <cache_dir>
This script analyzes the merges i.e. it checks if the parents pass tests and it
computes statistics between merges.
The output is written in output_dir and consists of the same merges as the input
but with the test results and statistics.
"""

import os
import multiprocessing
import argparse
from pathlib import Path
from typing import Tuple
import random
import pandas as pd
from repo import Repository, TEST_STATE
from cache_utils import set_in_cache, lookup_in_cache
from test_repo_heads import num_processes
from variables import TIMEOUT_TESTING_PARENT, N_TESTS
import matplotlib.pyplot as plt
from loguru import logger
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TextColumn,
)
from diff_statistics import (
    compute_num_different_files,
    compute_num_different_lines,
    compute_are_imports_involved,
    diff_contains_non_java_file,
    diff_contains_java_file,
    compute_num_diff_hunks,
    compute_union_of_different_files_three_way,
    compute_intersection_of_diff,
)


def is_test_passed(test_state: str) -> bool:
    """Returns true if the test state indicates passed tests."""
    return test_state == TEST_STATE.Tests_passed.name


def merge_analyzer(
    args: Tuple[str, str, pd.Series, Path],
) -> pd.Series:
    """
    Merges two branches and returns the result.
    Args:
        args (Tuple[str,str,pd.Series,Path]): A tuple containing the merge index, the repo slug,
                the merge data (which is side-effected), and the cache path.
    Returns:
        dict: A dictionary containing the merge result.
    """
    merge_idx, repo_slug, merge_data, cache_directory = args

    cache_key = f"{merge_data['left']}_{merge_data['right']}"
    cache_data = lookup_in_cache(
        cache_key, repo_slug, cache_directory / "merge_analysis", True
    )
    write = False

    if cache_data is None:
        cache_data = {}
    if isinstance(cache_data, str):
        raise ValueError(
            f"merge_analyzer: Expected a dictionary, got a string: {cache_data}"
        )

    repo = None
    stats = (
        ("num_diff_files", compute_num_different_files),
        ("union_diff_files", compute_union_of_different_files_three_way),
        ("num_intersecting_files", compute_intersection_of_diff),
        ("num_diff_lines", compute_num_different_lines),
        ("num_diff_hunks", compute_num_diff_hunks),
        ("imports_involved", compute_are_imports_involved),
        ("non_java_involved", diff_contains_non_java_file),
        ("diff contains java file", diff_contains_java_file),
    )
    repo = None
    for name, func in stats:
        if name not in cache_data:
            if repo is None:
                repo = Repository(
                    merge_idx,
                    repo_slug,
                    cache_directory=cache_directory,
                    workdir_id=repo_slug
                    + "/stats-"
                    + merge_data["left"]
                    + "-"
                    + merge_data["right"],
                )
            # Pass in base sha for union and intersection stats.
            if name == "union_diff_files" or name == "num_intersecting_files":
                command = (
                    "git merge-base "
                    + str(merge_data["left"])
                    + " "
                    + str(merge_data["right"])
                )
                try:
                    base_sha = repo.run_command(command)[0].strip()
                except Exception as e:
                    logger.error(
                        f"merge_analyzer: Error while running command: {command}"
                    )
                    logger.error(f"merge_analyzer: Error: {e}")
                    cache_data[name] = "Error while retrieving base sha"
                    write = True
                    continue
                cache_data[name] = func(
                    repo, base_sha, merge_data["left"], merge_data["right"]
                )
            else:
                cache_data[name] = func(repo, merge_data["left"], merge_data["right"])
            merge_data[name] = cache_data[name]
            write = True

    left_sha = merge_data["left"]
    right_sha = merge_data["right"]

    logger.info(
        f"merge_analyzer: Analyzing {merge_idx} {repo_slug} {left_sha} {right_sha}"
    )

    if cache_data["diff contains java file"] in (False, None):
        if (
            "test merge" not in cache_data
            or "diff contains java file" not in cache_data
        ):
            cache_data["test merge"] = False
            cache_data["diff contains java file"] = False
            write = True
        logger.info(
            f"merge_analyzer: Analyzed {merge_idx} {repo_slug} {left_sha} {right_sha}"
        )
        if write:
            set_in_cache(
                cache_key, cache_data, repo_slug, cache_directory / "merge_analysis"
            )
        for key in cache_data:
            if key != "diff_logs":
                merge_data[key] = cache_data[key]
        return merge_data

    if "parents pass" not in cache_data:
        write = True
        # Checkout left parent
        repo_left = Repository(
            merge_idx,
            repo_slug,
            cache_directory=cache_directory,
            workdir_id=repo_slug + "/left-" + left_sha + "-" + right_sha,
            lazy_clone=True,
        )

        # Checkout right parent
        repo_right = Repository(
            merge_idx,
            repo_slug,
            cache_directory=cache_directory,
            workdir_id=repo_slug + "/right-" + left_sha + "-" + right_sha,
            lazy_clone=True,
        )

        # Test left parent
        result, _, left_tree_fingerprint = repo_left.checkout_and_test(
            left_sha, TIMEOUT_TESTING_PARENT, N_TESTS
        )
        cache_data["left_tree_fingerprint"] = left_tree_fingerprint
        cache_data["left parent test result"] = result.name

        # Test right parent
        result, _, right_tree_fingerprint = repo_right.checkout_and_test(
            right_sha, TIMEOUT_TESTING_PARENT, N_TESTS
        )
        cache_data["right_tree_fingerprint"] = right_tree_fingerprint
        cache_data["right parent test result"] = result.name

        # Produce the final result
        print(
            f"merge_analyzer: {merge_idx} {repo_slug} {left_sha} {right_sha} {cache_data['left parent test result']} {cache_data['right parent test result']}"
        )
        cache_data["parents pass"] = is_test_passed(
            cache_data["left parent test result"]
        ) and is_test_passed(cache_data["right parent test result"])
        cache_data["diff contains java file"] = cache_data["diff contains java file"]
        cache_data["test merge"] = (
            cache_data["parents pass"] and cache_data["diff contains java file"] is True
        )

        logger.info(
            f"merge_analyzer: Analyzed {merge_idx} {repo_slug} {left_sha} {right_sha}"
        )
    if write:
        set_in_cache(
            cache_key, cache_data, repo_slug, cache_directory / "merge_analysis"
        )

    for key in cache_data:
        if key != "diff_logs":
            merge_data[key] = cache_data[key]

    return merge_data


def build_merge_analyzer_arguments(
    repo_idx: str, args: argparse.Namespace, repo_slug: str
):
    """
    Creates the arguments for the merger function.
    Args:
        reo_idx (str): The repository index.
        args (argparse.Namespace): The arguments to the script.
        repo_slug (str): The repository slug.
    Returns:
        list: A list of arguments for the merger function.
    """
    merge_list_file = Path(os.path.join(args.merges_path, repo_slug + ".csv"))
    if not merge_list_file.exists():
        raise Exception(
            "merge_analyzer: The repository does not have a list of merges.",
            repo_slug,
            merge_list_file,
        )

    merges = pd.read_csv(
        merge_list_file,
        names=["idx", "branch_name", "merge", "left", "right", "notes"],
        dtype={
            "idx": int,
            "branch_name": str,
            "merge": str,
            "left": str,
            "right": str,
            "notes": str,
        },
        header=0,
        index_col="idx",
    )
    merges["left"] = merges["left"].astype(str)
    merges["right"] = merges["right"].astype(str)
    merges["notes"] = merges["notes"].fillna("")

    arguments = [
        (f"{repo_idx}-{idx}", repo_slug, merge_data, Path(args.cache_dir))
        for idx, merge_data in merges.iterrows()
    ]
    return arguments


# Plotting function using matplotlib
def plot_vertical_histogram(data, title, ax):
    """Plot a vertical histogram with the given data"""
    data = [
        data[i] for i in sorted(range(len(data)), key=lambda i: data[i], reverse=True)
    ]
    ax.bar(range(len(data)), data)
    ax.set_title(title)
    ax.set_xlabel("Repository Index")
    ax.set_ylabel("Count")


if __name__ == "__main__":
    logger.info("merge_analyzer: Start")
    parser = argparse.ArgumentParser()
    parser.add_argument("--repos_head_passes_csv", type=Path)
    parser.add_argument("--merges_path", type=Path)
    parser.add_argument("--output_dir", type=Path)
    parser.add_argument("--cache_dir", type=Path, default="cache/merge_diffs/")
    parser.add_argument("--n_sampled_merges", type=int, default=20)
    args = parser.parse_args()
    Path(args.cache_dir).mkdir(parents=True, exist_ok=True)
    (Path(args.cache_dir) / "merge_analysis").mkdir(parents=True, exist_ok=True)
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    repos = pd.read_csv(args.repos_head_passes_csv, index_col="idx")

    logger.info("merge_analyzer: Constructing Inputs")
    merger_arguments = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("[green]Constructing Input...", total=len(repos))
        for repo_idx, repository_data in repos.iterrows():
            repo_slug = repository_data["repository"]
            merger_arguments += build_merge_analyzer_arguments(
                str(repo_idx), args, repo_slug
            )
            progress.update(task, advance=1)

    # Shuffle input to reduce cache contention
    random.seed(42)
    random.shuffle(merger_arguments)

    logger.info("merge_analyzer: Finished Constructing Inputs")
    # New merges are merges whose analysis does not appear in the output folder.
    logger.info("merge_analyzer: Number of new merges: " + str(len(merger_arguments)))

    logger.info("merge_analyzer: Started Merging")
    with multiprocessing.Pool(processes=num_processes()) as pool:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task("[green]Analyzing...", total=len(merger_arguments))
            merger_results = []
            for result in pool.imap(merge_analyzer, merger_arguments):
                merger_results.append(result)
                progress.update(task, advance=1)
    logger.info("merge_analyzer: Finished Merging")

    repo_result = {repo_slug: [] for repo_slug in repos["repository"]}
    logger.info("merge_analyzer: Constructing Output")
    n_new_analyzed = 0
    n_new_candidates_to_test = 0
    n_new_passing_parents = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("[green]Processing...", total=len(merger_arguments))
        for new_merges_idx, merge_data in enumerate(merger_arguments):
            repo_slug = merge_data[1]
            results_data = merger_results[new_merges_idx]
            repo_result[repo_slug].append(merger_results[new_merges_idx])
            n_new_analyzed += 1
            if "test merge" in results_data and results_data["test merge"]:
                n_new_candidates_to_test += 1
            if "parents pass" in results_data and results_data["parents pass"]:
                n_new_passing_parents += 1
            progress.update(task, advance=1)

    # Initialize counters
    n_total_analyzed = 0
    n_candidates_to_test = 0
    n_java_contains_diff = 0
    n_sampled_for_testing = 0

    # Data collection for histograms
    repo_data = []

    for repo_slug in repo_result:
        output_file = Path(os.path.join(args.output_dir, repo_slug + ".csv"))

        df = pd.DataFrame(repo_result[repo_slug])
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if len(df) == 0:
            df.to_csv(output_file, index_label="idx")
            continue

        # Pick randomly n_sampled_merges merges to test from the ones that are candidates
        df["sampled for testing"] = False
        testable_merges = df[df["test merge"]]
        testable_merges = testable_merges.sample(frac=1.0, random_state=42)
        sampled_merges = testable_merges[: args.n_sampled_merges]
        df.loc[sampled_merges.index, "sampled for testing"] = True

        df.sort_index(inplace=True)
        df.to_csv(output_file, index_label="idx")

        # Collect data for histograms
        repo_data.append(
            (
                repo_slug,
                len(df),
                df["test merge"].sum(),
                df["diff contains java file"].dropna().sum(),
                df["sampled for testing"].sum(),
            )
        )

        # Update global counters
        n_total_analyzed += len(df)
        n_java_contains_diff += df["diff contains java file"].dropna().sum()
        n_candidates_to_test += df["test merge"].sum()
        n_sampled_for_testing += df["sampled for testing"].sum()

    # Print summaries
    logger.success(
        "merge_analyzer: Total number of merges that have been compared: "
        + str(n_total_analyzed)
    )
    logger.success(
        "merge_analyzer: Total number of merges that have been compared and have a java diff: "
        + str(n_java_contains_diff)
    )
    logger.success(
        "merge_analyzer: Total number of merges that have been "
        "compared and are testable (Has Java Diff + Parents Pass) "
        + str(n_candidates_to_test)
    )
    logger.success(
        "merge_analyzer: Total number of merges that are testable which have been sampled "
        + str(n_sampled_for_testing)
    )
    logger.info("merge_analyzer: Finished Constructing Output")

    # Creating the plots
    repo_slugs, totals, candidates, passings, sampled = zip(*repo_data)
    fig, axs = plt.subplots(4, 1, figsize=(10, 20), tight_layout=True)
    plot_vertical_histogram(
        totals,
        f"Total Analyzed per Repository (Total: {n_total_analyzed})",
        axs[0],
    )
    plot_vertical_histogram(
        candidates,
        f"Merges which contain a Java file that has been changed (Total: {n_java_contains_diff})",
        axs[1],
    )
    plot_vertical_histogram(
        passings,
        f"Testable (Has Java Diff + Parents Pass) merge Candidates "
        f"per Repository (Total: {n_candidates_to_test})",
        axs[2],
    )
    plot_vertical_histogram(
        sampled,
        f"Sampled Merges for Testing per Repository (Total: {n_sampled_for_testing})",
        axs[3],
    )

    # Add titles and save the figure
    fig.suptitle(
        f"Merges Analysis Results (Number of Repositories: {len(repos)})", y=0.98
    )
    fig.tight_layout(rect=[0, 0, 1, 0.97])  # type: ignore

    parent_output_dir = args.output_dir.parent
    plt.savefig(parent_output_dir / "merges_analyzer_histograms.pdf")
    logger.success("merge_analyzer: Done")
