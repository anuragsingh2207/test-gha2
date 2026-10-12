# Public Gists API

A small FastAPI service that returns a GitHub user's publicly available Gists.

## Run locally

Requires Python 3.12 or later.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Request `GET http://localhost:8080/octocat`. The response is the GitHub Gists JSON array. The service fetches all pages from GitHub; no GitHub token is required for public Gists. `GET /health` is available for health checks.

Optional environment configuration:

```sh
export GITHUB_TOKEN=your-github-token
export GITHUB_API_BASE_URL=https://api.github.com
```

`GITHUB_TOKEN` can be set to provide authenticated GitHub API access and higher rate limits. `GITHUB_API_BASE_URL` defaults to `https://api.github.com`.

## Tests and checks

```sh
pip install -r requirements-dev.txt
pytest
ruff check .
bandit -q -r app
pip-audit -r requirements.txt
```

Tests mock GitHub's API, so they do not require network access or credentials.
Pytest reports application coverage and fails unless it exceeds 80%, both
locally and in CI.

## Container

```sh
docker build -t public-gists-api .
docker run --rm -p 8080:8080 public-gists-api
```

The image uses a multi-stage build, runs as a non-root user, and listens on
port `8080`. Development files and local environment files are excluded from
the build context.

## CI security checks

GitHub Actions runs the tests and static checks, audits Python dependencies with `pip-audit`, scans the built image for known vulnerabilities with Trivy, and scans repository history for hardcoded credentials with Gitleaks.

## Best practices followed

- Keep the service focused and use FastAPI with asynchronous HTTP requests to GitHub.
- Validate GitHub usernames before making upstream requests.
- Configure request timeouts and return explicit errors for GitHub failures, rate limits, and invalid responses.
- Retrieve all Gist pages rather than silently limiting results to the first page.
- Test API behavior with a mocked GitHub transport, avoiding network access and credentials.
- Enforce a minimum test coverage threshold and run linting and static security checks.
- Use a multi-stage container build, run the service as a non-root user, and exclude development files from the image build context.
- Check dependencies, the built container image, and repository history in CI for known vulnerabilities and hardcoded credentials.
- Read GitHub API credentials and endpoint configuration from environment variables rather than embedding them in source code.
- Log upstream failures with the affected username and page to help diagnose operational issues.
