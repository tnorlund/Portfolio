from pulumi import automation as auto


def load_env(env: str = "dev") -> dict:
    """Retrieves Pulumi stack outputs for the specified environment.

    Args:
        env (str, optional): Pulumi environment (stack) name, e.g. "dev" or "prod".
            Defaults to "dev".

    Returns:
        dict: A dictionary of key-value pairs from the Pulumi stack outputs.
    """
    stack = auto.select_stack(
        stack_name=f"tnorlund/portfolio/{env}",
        project_name="portfolio",
        program=lambda: None,
    )
    return {key: val.value for key, val in stack.outputs().items()}
