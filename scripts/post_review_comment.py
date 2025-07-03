#!/usr/bin/env python3
"""
Post Claude review comments to GitHub PRs with smart comment management.
"""

import os
import re
from pathlib import Path
from typing import Optional

import click
from github import Github


class ReviewCommentManager:
    def __init__(self, github_token: str, repository: str):
        self.github = Github(github_token)
        self.repo = self.github.get_repo(repository)
        self.comment_marker = "<!-- CLAUDE-CODE-REVIEW -->"

    def find_existing_review_comment(self, pr_number: int) -> Optional[int]:
        """Find existing Claude review comment."""
        pr = self.repo.get_pull_request(pr_number)

        for comment in pr.get_issue_comments():
            if self.comment_marker in comment.body:
                return comment.id

        return None

    def post_or_update_review(
        self, pr_number: int, review_content: str
    ) -> bool:
        """Post new review or update existing one."""
        try:
            pr = self.repo.get_pull_request(pr_number)

            # Add marker to content
            marked_content = f"{self.comment_marker}\n{review_content}"

            # Check for existing comment
            existing_comment_id = self.find_existing_review_comment(pr_number)

            if existing_comment_id:
                # Update existing comment
                comment = pr.get_issue_comment(existing_comment_id)
                comment.edit(marked_content)
                print(
                    f"✅ Updated existing Claude review comment #{existing_comment_id}"
                )
            else:
                # Create new comment
                comment = pr.create_issue_comment(marked_content)
                print(f"✅ Posted new Claude review comment #{comment.id}")

            return True

        except Exception as e:
            print(f"❌ Error posting review comment: {e}")
            return False


@click.command()
@click.option("--pr-number", required=True, type=int, help="PR number")
@click.option(
    "--repository", required=True, help="Repository in format owner/repo"
)
@click.option(
    "--review-file", required=True, help="File containing review content"
)
@click.option(
    "--update-existing", is_flag=True, help="Update existing comment if found"
)
def main(
    pr_number: int, repository: str, review_file: str, update_existing: bool
):
    """Post Claude review comment to GitHub PR."""

    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        click.echo("❌ GITHUB_TOKEN environment variable required", err=True)
        return 1

    review_path = Path(review_file)
    if not review_path.exists():
        click.echo(f"❌ Review file not found: {review_file}", err=True)
        return 1

    try:
        # Read review content
        review_content = review_path.read_text()

        # Initialize comment manager
        manager = ReviewCommentManager(github_token, repository)

        # Post or update review
        success = manager.post_or_update_review(pr_number, review_content)

        if success:
            click.echo(
                f"✅ Successfully posted Claude review to PR #{pr_number}"
            )
        else:
            click.echo(f"❌ Failed to post review comment")
            return 1

    except Exception as e:
        click.echo(f"❌ Error: {e}", err=True)
        return 1


if __name__ == "__main__":
    main()
