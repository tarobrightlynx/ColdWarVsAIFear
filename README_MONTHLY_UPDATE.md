# Monthly AI Fear Dashboard for GitHub Pages

This package turns the dashboard into an auto-updating GitHub Pages project.

## What it does

Every month, GitHub Actions will:

1. run `scrape_ai_articles.py` to discover and scrape recent AI articles from the last 4 weeks,
2. run `categorize_scraped_texts.py` to score fear intensity and the 10 fear categories,
3. run `update_dashboard_from_results.py` to update the latest AI values embedded in `index.html`, and
4. commit the updated `index.html` and monthly CSV archive back to the repository.

## Files to upload to your repository

Upload everything in this folder to the root of your GitHub repository:

- `index.html`
- `scrape_ai_articles.py`
- `categorize_scraped_texts.py`
- `update_dashboard_from_results.py`
- `requirements.txt`
- `.github/workflows/monthly-ai-dashboard.yml`

## Required GitHub secret

Create this repository secret:

- Name: `OPENAI_API_KEY`
- Value: your OpenAI API key

Go to your repository settings, then open **Secrets and variables → Actions → New repository secret**.

## Turn on GitHub Pages

Use **Settings → Pages** and publish from:

- Branch: `main`
- Folder: `/root`

Your site URL will usually be:

`https://YOUR-USERNAME.github.io/YOUR-REPOSITORY-NAME/`

## Test it manually

After uploading the files and setting the secret:

1. Open the **Actions** tab.
2. Choose **Monthly AI Fear Dashboard Update**.
3. Click **Run workflow**.
4. After it finishes, refresh your GitHub Pages site.

## Cost note

The workflow uses `CODING_RUNS: "5"` by default to reduce API cost and runtime. The paper method used repeated model runs. To match the paper more closely, change this line in `.github/workflows/monthly-ai-dashboard.yml`:

```yaml
CODING_RUNS: "25"
```

## Optional BigQuery mode

The workflow uses Google News discovery by default because it does not require Google Cloud credentials. Your scraper also supports BigQuery discovery. To use BigQuery, change:

```yaml
DISCOVERY_PROVIDER: "bigquery"
```

and add the needed Google Cloud authentication setup for your repository.
