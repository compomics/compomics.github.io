"""
Download GitHub README files and wiki documentation, and parse for
compomics.github.io pages.

- Removes ./pages/<project_name> directory and all of its contents
- Download README.md and wiki pages
- Parse markdownfiles
    - Adapt wiki URLs to https://<user>.github.io/project/wiki/<file>
- Add required Jekyll front-end matter
"""

import re
import os
import shutil
import base64
import logging
import argparse
import urllib.parse
from glob import glob
from pathlib import Path

import yaml
import git
import rst_to_myst
from github import Github, Auth


# Globals
LOG_LEVEL = logging.INFO
PUSH_USER = "compomics"
PUSH_REPO_NAME = "compomics.github.io"


def argument_parser():
    parser = argparse.ArgumentParser(description='Download GitHub README files\
        and wiki documentation, and parse for compomics.github.io pages.\
        Use YAML config file or CLI arguments.')
    parser.add_argument('-c', dest='path_to_config', action='store',
                            help='YAML configuration file')
    parser.add_argument('-t', dest='github_token', action='store',
                        help='GitHub API access token')
    parser.add_argument('-p', dest='project', action='store',
                        help='Name of GitHub repository to parse')
    parser.add_argument('-u', dest='user', action='store',
                        help='Name of GitHub profile to parse from.')
    parser.add_argument('-a', action='store_true', default=False,
                        dest='update_all', help='Update all existing projects')
    args = parser.parse_args()
    return args


def load_config(args):
    """
    Load YAML config file
    """
    if args.path_to_config:
        with open(args.path_to_config, 'r') as stream:
            config = yaml.safe_load(stream)
    else:
        config = dict()

    if args.github_token:
        config['github_token'] = args.github_token
    if args.project:
        config['projects'] = [args.project]
    elif args.update_all:
        exising_projects = [p.split('/')[1] for p in glob('pages/*')]
        config['projects'] = exising_projects
    if args.user:
        config['user'] = args.user

    # Check if config options are passed
    for k in ['github_token', 'projects', 'user']:
        if k not in config:
            logging.error("`%s` not specified. Use either YAML or CLI arguments\
 to specifiy `%s`", k, k)
            exit(1)

    return config


def construct_project_home_header(project_name, repo_meta):
    """
    Return project_home Jekyll front matter header for compomics.github.io
    pages.
    """
    args = {
        'project_name': project_name,
        'html_url': repo_meta['html_url'],
        'description': repo_meta['description']
    }

    header_string = """---
title: "{project_name}"
project: "{project_name}"
github_project: "{html_url}"
description: "{description}"
layout: default
tags: project_home, {project_name}
permalink: /projects/{project_name}
---

""".format(**args)

    return header_string


def parse_wiki_title(filename):
    title = filename\
        .replace('-', ' ')\
        .replace('.md', '')\
        .replace('.MD', '')
    return title


def parse_wiki_permalink(filename, project_name):
    parsed_name = filename\
        .replace('.md', '')\
        .replace('.MD', '')\
        .replace('.', '')\
        .replace(':', '')
    parsed_name = urllib.parse.quote(parsed_name)
    permalink = "/projects/{}/wiki/{}".format(project_name, parsed_name)
    return permalink


def construct_wiki_header(wiki_meta):
    """
    Return wiki page Jekyll front matter header for compomics.github.io pages.
    """
    header = """---
title: "{title}"
layout: default
permalink: "{permalink}"
tags: {tags}
project: "{project}"
github_project: "{github_project}"
---

""".format(**wiki_meta)
    return header


def url_sub(matches):
    """
    Function for re.sub() that returns a parsed URL
    """
    url = matches.group(1).rstrip("/")

    if url.endswith('wiki'):
        return '/projects' + url + ')'
    elif url.endswith('issues') or url.endswith('releases') or url.endswith('releases/latest'):
        return matches.group(0)
    else:
        return '/projects' + url + ')'


def file_parser(line):
    """
    Parse Markdown page for compomics.github.io pages:
     - Replace wiki URLs to pages URLs
    """
    line = re.sub(
        r'https:\/\/github\.com\/compomics([^.]*)\)',
        url_sub,
        line
    )
    return line


def get_readme_file(config, repo_meta, github_instance):
    """
    Download default branch README.md file from GitHub and parse for github.io
    pages.
    """
    project_name = config['project_name']
    project_dir = os.path.join("pages", project_name)

    # Get README contents of default branch
    repo = github_instance.get_repo("{}/{}".format(config['user'], config['project_name']))
    readme = repo.get_readme()
    readme_content = base64.b64decode(readme.content).decode()
    readme_suffix = Path(readme.download_url).suffix.lower()
    
    if readme_suffix == ".rst":
        readme_md = rst_to_myst.rst_to_myst(readme_content, use_sphinx=False).text
    elif readme_suffix == ".md":
        readme_md = readme_content
    else:
        raise ValueError("README in unsupported filetype")

    # Write to file
    with open(os.path.join(project_dir, project_name + '.md'), 'wt') as readme_out:
        readme_out.write(construct_project_home_header(project_name, repo_meta))
        readme_out.write(file_parser(readme_md))



def get_wiki_files(config, repo_meta):
    """
    Download and parse all wiki files to ./pages/<project_name>/wiki/...
    """
    # Remove tmp files (if exists)
    if os.path.isdir("tmp_wiki_clone"):
        shutil.rmtree("tmp_wiki_clone")

    # Remove existing wiki files
    wiki_dir = os.path.join('pages', config['project_name'], 'wiki')
    if os.path.isdir(wiki_dir):
        shutil.rmtree(wiki_dir)
    os.makedirs(wiki_dir)

    # Clone into tmp_wiki_clone
    wiki_url = "https://github.com/{}/{}.wiki.git".format(
        config['user'],
        config['project_name']
    )
    try:
        repo = git.Repo.clone_from(wiki_url, "tmp_wiki_clone")
    except:
        logging.warning("Failed to clone wiki repository. Is it empty? \
Disable wiki in GitHub repository options to prevent warning.")
        return None

    wiki_files = [f for f in os.listdir('tmp_wiki_clone') if f[-3:] == '.md']

    # Parse all metadata
    wiki_files = [{
        'filename': f,
        'title': parse_wiki_title(f),
        'project': config['project_name'],
        'permalink': parse_wiki_permalink(f, config['project_name']),
        'tags': "wiki, {}".format(config['project_name']),
        'github_project': repo_meta['html_url']
        } for f in wiki_files]

    for wiki_meta in wiki_files:
        f_in_name = os.path.join('tmp_wiki_clone', wiki_meta['filename'])
        f_out_name = os.path.join(wiki_dir, wiki_meta['filename'])

        with open(f_in_name, 'rt') as f_in:
            with open(f_out_name, 'wt') as f_out:
                f_out.write(construct_wiki_header(wiki_meta))
                for i, line in enumerate(f_in):
                    if i == 0:
                        if not line.startswith('# '):
                            f_out.write("# {}\n".format(wiki_meta['title']))
                    f_out.write(file_parser(line))

    # Remove tmp files
    shutil.rmtree("tmp_wiki_clone")


def main():
    # Set up logging
    logging.basicConfig(
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=LOG_LEVEL
    )

    # Get config
    args = argument_parser()
    config = load_config(args)

    # Login into GitHub
    auth = Auth.Token(config['github_token'])
    g = Github(auth=auth)

    for project in config['projects']:
        config['project_name'] = project

        # Get project meta data
        repo = g.get_repo("{}/{}".format(config['user'], config['project_name']))
        repo_meta = {
            'html_url': repo.html_url,
            'has_wiki': repo.has_wiki,
            'description': repo.description,
        }
        logging.info("Parsing %s/%s", config['user'], config['project_name'])

        # Remove existing project directory and make new dirs
        project_dir = os.path.join("pages", config['project_name'])
        if os.path.isdir(project_dir):
            shutil.rmtree(project_dir)
        os.makedirs(project_dir)

        # Get and parse README file (project home)
        get_readme_file(config, repo_meta, g)

        # Get and parse wiki files
        if repo_meta['has_wiki']:
            get_wiki_files(config, repo_meta)
        else:
            logging.info("Project has no wiki.")

        logging.info(
            "Succesfully parsed %s/%s", config['user'],
            config['project_name']
        )

    logging.info("Ready")

if __name__ == "__main__":
    main()
