# GitHub Workflows

Simple CI/CD setup running on laptop (self-hosted macOS ARM64 runner).

## Active Workflows

- **`main.yml`** - Main CI/CD pipeline (tests + deployment on push to main)
- **`pr-checks.yml`** - Quick PR validation with auto-formatting  
- **`deploy-on-main.yml`** - Standalone deployment workflow
- **`claude.yml`** - Basic Claude AI integration for PR comments

## Manual Triggers
- Push/merge to main → full CI + deploy
- PR events → quick validation 
- `@claude` in comments → AI assistance

## Secrets Required
- `ANTHROPIC_API_KEY` - Claude AI
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` - AWS deployment
- `PULUMI_ACCESS_TOKEN` - Infrastructure deployment