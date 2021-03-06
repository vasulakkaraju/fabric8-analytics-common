"""The main module of the QA Dashboard."""
import json
import time
import datetime
import os
import sys
import requests
import csv
import shutil

from coreapi import *
from jobsapi import *
from configuration import *
from results import *
from html_generator import *
from perf_tests import *
from smoke_tests import *
from sla import *
from ci_jobs import *
from cliargs import *


def check_environment_variable(env_var_name):
    """Check if the given environment variable exists."""
    print("Checking: {e} environment variable existence".format(
        e=env_var_name))
    if env_var_name not in os.environ:
        print("Fatal: {e} environment variable has to be specified"
              .format(e=env_var_name))
        sys.exit(1)
    else:
        print("    ok")


def check_environment_variables():
    """Check if all required environment variables exist."""
    environment_variables = [
        "F8A_API_URL_STAGE",
        "F8A_API_URL_PROD",
        "F8A_JOB_API_URL_STAGE",
        "F8A_JOB_API_URL_PROD",
        "RECOMMENDER_API_TOKEN_STAGE",
        "RECOMMENDER_API_TOKEN_PROD",
        "JOB_API_TOKEN_STAGE",
        "JOB_API_TOKEN_PROD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "S3_REGION_NAME"]
    for environment_variable in environment_variables:
        check_environment_variable(environment_variable)


def check_system(core_api, jobs_api):
    """Check if all system endpoints are available and that tokens are valid."""
    # try to access system endpoints
    print("Checking: core API and JOBS API endpoints")
    core_api_available = core_api.is_api_running()
    jobs_api_available = jobs_api.is_api_running()

    if core_api_available and jobs_api_available:
        print("    ok")
    else:
        print("    Fatal: tested system is not available")

    # check the authorization token for the core API
    print("Checking: authorization token for the core API")
    core_api_auth_token = core_api.check_auth_token_validity()

    if core_api_auth_token:
        print("    ok")
    else:
        print("    error")

    # check the authorization token for the jobs API
    print("Checking: authorization token for the jobs API")
    jobs_api_auth_token = jobs_api.check_auth_token_validity()

    if jobs_api_auth_token:
        print("    ok")
    else:
        print("    error")

    return {"core_api_available": core_api_available,
            "jobs_api_available": jobs_api_available,
            "core_api_auth_token": core_api_auth_token,
            "jobs_api_auth_token": jobs_api_auth_token}


repositories = [
    "fabric8-analytics-common",
    "fabric8-analytics-server",
    "fabric8-analytics-worker",
    "fabric8-analytics-jobs",
    "fabric8-analytics-tagger",
    "fabric8-analytics-stack-analysis",
    "fabric8-analytics-license-analysis",
    "fabric8-analytics-data-model",
    "fabric8-analytics-recommender"
]

ci_job_types = [
    "test_job",
    "build_job",
    "pylint_job",
    "pydoc_job"
]

JENKINS_URL = "https://ci.centos.org"
JOBS_STATUSES_FILENAME = "jobs.json"


def is_repository_cloned(repository):
    """Check if the directory with cloned repository exist."""
    return os.path.isdir(repository)


def clone_repository(repository):
    """Clone the selected repository."""
    print("Cloning the repository {repository}".format(repository=repository))
    prefix = "https://github.com/fabric8-analytics"
    command = "git clone --single-branch --depth 1 {prefix}/{repo}.git".format(prefix=prefix,
                                                                               repo=repository)
    os.system(command)


def fetch_repository(repository):
    """Fetch the selected repository."""
    print("Fetching changes from the repository {repository}".format(repository=repository))
    command = "pushd {repository}; git fetch; popd".format(repository=repository)
    os.system(command)


def clone_or_fetch_repository(repository):
    """Clone or fetch the selected repository."""
    if is_repository_cloned(repository):
        fetch_repository(repository)
    else:
        clone_repository(repository)


def run_pylint(repository):
    """Run Pylint checker against the selected repository."""
    command = "pushd {repo};./run-linter.sh > ../{repo}.linter;popd".format(repo=repository)
    os.system(command)


def run_docstyle_check(repository):
    """Run PyDocsStyle checker against the selected repository."""
    command = "pushd {repo};./check-docstyle.sh > ../{repo}.pydocstyle;popd".format(repo=repository)
    os.system(command)


def progress_bar_class(p):
    """Decide which class to use for progress bar."""
    p = int(p)
    if p < 10:
        return "progress-bar-danger"
    elif p > 90:
        return "progress-bar-success"
    else:
        return "progress-bar-warning"


def progress_bar_width(p):
    """Compute progress bar width."""
    p = int(p)
    return 15.0 + p * 0.85


def percentage(part1, part2):
    """Compute percentage of failed tests."""
    total = part1 + part2
    if total == 0:
        return "0"
    perc = 100.0 * part2 / total
    return "{:.0f}".format(perc)


def parse_linter_results(filename):
    """Parse results generated by Python linter or by PyDocStyle."""
    source = None

    files = {}
    passed = 0
    failed = 0
    total = 0

    with open(filename) as fin:
        for line in fin:
            line = line.rstrip()
            if line.endswith(".py"):
                source = line.strip()
            if line.endswith("    Pass"):
                if source:
                    passed += 1
                    total += 1
                    files[source] = True
            if line.endswith("    Fail"):
                if source:
                    failed += 1
                    total += 1
                    files[source] = False

    return {"files": files,
            "total": total,
            "passed": passed,
            "failed": failed,
            "passed%": percentage(failed, passed),
            "failed%": percentage(passed, failed),
            "progress_bar_class": progress_bar_class(percentage(failed, passed)),
            "progress_bar_width": progress_bar_width(percentage(failed, passed))}


def parse_pylint_results(repository):
    """Parse results generated by Python linter."""
    return parse_linter_results(repository + ".linter")


def parse_docstyle_results(repository):
    """Parse results generated by PyDocStyle."""
    return parse_linter_results(repository + ".pydocstyle")


def parse_line_count(line):
    """Parse the information with line counts."""
    line = line.strip()
    line_count, filename = line.split(" ")
    # remove prefix that is not relevant much
    if filename.startswith("./"):
        filename = filename[len("./"):]
    return int(line_count), filename


def get_source_files(repository):
    """Find all source files in the selected repository."""
    command = ("pushd {repo}; wc -l `find . -path ./venv -prune -o -name '*.py' -print` " +
               "| head -n -1 > ../{repo}.count;popd").format(repo=repository)
    os.system(command)
    filenames = []
    line_counts = {}
    total_lines = 0
    count = 0

    with open("{repo}.count".format(repo=repository)) as fin:
        for line in fin:
            count += 1
            line_count, filename = parse_line_count(line)
            filenames.append(filename)
            line_counts[filename] = line_count
            total_lines += line_count

    return {"count": count,
            "filenames": filenames,
            "line_counts": line_counts,
            "total_lines": total_lines}


def update_overall_status(results, repository):
    """Update the overall status of all tested systems (stage, prod)."""
    remarks = ""
    status = False

    source_files = results.source_files[repository]["count"]
    linter_checks = results.repo_linter_checks[repository]
    docstyle_checks = results.repo_docstyle_checks[repository]

    linter_checks_total = linter_checks["total"]
    docstyle_checks_total = docstyle_checks["total"]

    if source_files == linter_checks_total and \
       source_files == docstyle_checks_total:
        if linter_checks["failed"] == 0 and docstyle_checks["failed"] == 0:
            status = True
    else:
        remarks = "not all source files are checked"
        if linter_checks_total != docstyle_checks_total:
            remarks += ", linter checked {n1} files, but pydocstyle checked {n2} files".format(
                n1=linter_checks_total, n2=docstyle_checks_total)

    results.overall_status[repository] = status
    results.remarks[repository] = remarks


def delete_work_files(repository):
    """Cleanup the CWD from the work files used to analyze given repository."""
    os.remove("{repo}.count".format(repo=repository))
    os.remove("{repo}.linter".format(repo=repository))
    os.remove("{repo}.pydocstyle".format(repo=repository))


def cleanup_repository(repository):
    """Cleanup the directory with the clone of specified repository."""
    # let's do very basic check that the repository is really local dir
    if '/' not in repository:
        print("cleanup " + repository)
        shutil.rmtree(repository, ignore_errors=True)


def export_into_csv(results):
    """Export the results into CSV file."""
    record = [
        datetime.date.today().strftime("%Y-%m-%d"),
        int(results.stage["core_api_available"]),
        int(results.stage["jobs_api_available"]),
        int(results.stage["core_api_auth_token"]),
        int(results.stage["jobs_api_auth_token"]),
        int(results.production["core_api_available"]),
        int(results.production["jobs_api_available"]),
        int(results.production["core_api_auth_token"]),
        int(results.production["jobs_api_auth_token"])
    ]

    for repository in repositories:
        record.append(results.source_files[repository]["count"])
        record.append(results.source_files[repository]["total_lines"])
        record.append(results.repo_linter_checks[repository]["total"])
        record.append(results.repo_linter_checks[repository]["passed"])
        record.append(results.repo_linter_checks[repository]["failed"])
        record.append(results.repo_docstyle_checks[repository]["total"])
        record.append(results.repo_docstyle_checks[repository]["passed"])
        record.append(results.repo_docstyle_checks[repository]["failed"])

    with open('dashboard.csv', 'a') as fout:
        writer = csv.writer(fout)
        writer.writerow(record)


def prepare_data_for_liveness_table(results):
    """Prepare data for sevices liveness/readiness table on the dashboard."""
    cfg = Configuration()

    core_api = CoreApi(cfg.stage.core_api_url, cfg.stage.core_api_token)
    jobs_api = JobsApi(cfg.stage.jobs_api_url, cfg.stage.jobs_api_token)
    results.stage = check_system(core_api, jobs_api)

    core_api = CoreApi(cfg.prod.core_api_url, cfg.prod.core_api_token)
    jobs_api = JobsApi(cfg.prod.jobs_api_url, cfg.prod.jobs_api_token)
    results.production = check_system(core_api, jobs_api)

    smoke_tests = SmokeTests()
    results.smoke_tests_results = smoke_tests.results


def prepare_data_for_sla_table(results):
    """Prepare data for SLA table on the dashboard."""
    perf_tests = PerfTests()
    perf_tests.read_results()
    perf_tests.compute_statistic()
    results.perf_tests_results = perf_tests.results
    results.perf_tests_statistic = perf_tests.statistic

    results.sla_thresholds = SLA


def prepare_data_for_repositories(repositories, results,
                                  clone_repositories_enabled, cleanup_repositories_enabled,
                                  code_quality_table_enabled, ci_jobs_table_enabled):
    """Perform clone/fetch repositories + run pylint + run docstyle script + accumulate results."""
    if ci_jobs_table_enabled:
        ci_jobs = CIJobs()
        job_statuses = read_ci_jobs_statuses(JENKINS_URL)

    for repository in repositories:

        # clone or fetch the repository if the cloning/fetching is not disabled via CLI arguments
        if clone_repositories_enabled:
            clone_or_fetch_repository(repository)

        if code_quality_table_enabled:
            run_pylint(repository)
            run_docstyle_check(repository)

            results.source_files[repository] = get_source_files(repository)
            results.repo_linter_checks[repository] = parse_pylint_results(repository)
            results.repo_docstyle_checks[repository] = parse_docstyle_results(repository)
            update_overall_status(results, repository)

        # delete_work_files(repository)

        if cleanup_repositories_enabled:
            cleanup_repository(repository)

        if ci_jobs_table_enabled:
            for job_type in ci_job_types:
                url = ci_jobs.get_job_url(repository, job_type)
                name = ci_jobs.get_job_name(repository, job_type)
                job_status = job_statuses.get(name)
                results.ci_jobs_links[repository][job_type] = url
                results.ci_jobs_statuses[repository][job_type] = job_status


def read_jobs_statuses(filename):
    """Deserialize statuses for all jobs from the JSON file."""
    with open(filename) as fin:
        return json.load(fin)["jobs"]


def store_jobs_statuses(filename, data):
    """Serialize statuses of all jobs into the JSON file."""
    with open(filename, "w") as fout:
        fout.write(data)


def jenkins_api_query(jenkins_url):
    """Construct API query to Jenkins (CI)."""
    return "{url}/api/json?tree=jobs[name,color]".format(url=jenkins_url)


def jobs_as_dict(raw_jobs):
    """Construct a dictionary with job name as key and job status as value."""
    return dict((job["name"], job["color"]) for job in raw_jobs if "color" in job)


def read_ci_jobs_statuses(jenkins_url):
    """Read statuses of all jobs from the Jenkins (CI)."""
    api_query = jenkins_api_query(jenkins_url)
    response = requests.get(api_query)
    raw_jobs = response.json()["jobs"]

    # for debugging purposes only
    # store_jobs_statuses(JOBS_STATUSES_FILENAME, response.text)

    # raw_jobs = read_jobs_statuses(JOBS_STATUSES_FILENAME)
    return jobs_as_dict(raw_jobs)


def main():
    """Entry point to the QA Dashboard."""
    cli_arguments = cli_parser.parse_args()

    # some CLI arguments are used to DISABLE given feature of the dashboard,
    # but let's not use double negation everywhere :)
    ci_jobs_table_enabled = not cli_arguments.disable_ci_jobs
    code_quality_table_enabled = not cli_arguments.disable_code_quality
    liveness_table_enabled = not cli_arguments.disable_liveness
    sla_table_enabled = not cli_arguments.disable_sla
    clone_repositories_enabled = cli_arguments.clone_repositories
    cleanup_repositories_enabled = cli_arguments.cleanup_repositories

    check_environment_variables()
    results = Results()

    # list of repositories to check
    results.repositories = repositories

    # we need to know which tables are enabled or disabled to proper process the template
    results.sla_table_enabled = sla_table_enabled
    results.liveness_table_enabled = liveness_table_enabled
    results.code_quality_table_enabled = code_quality_table_enabled
    results.ci_jobs_table_enabled = ci_jobs_table_enabled

    if liveness_table_enabled:
        prepare_data_for_liveness_table(results)

    prepare_data_for_repositories(repositories, results,
                                  clone_repositories_enabled, cleanup_repositories_enabled,
                                  code_quality_table_enabled, ci_jobs_table_enabled)

    if sla_table_enabled:
        prepare_data_for_sla_table(results)

    if code_quality_table_enabled and liveness_table_enabled:
        export_into_csv(results)

    generate_dashboard(results)


if __name__ == "__main__":
    # execute only if run as a script
    main()
