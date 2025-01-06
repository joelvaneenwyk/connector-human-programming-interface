#!/usr/bin/env python3
# see https://github.com/karlicoss/pymplate for up-to-date reference

from setuptools import setup, find_namespace_packages # type: ignore

def main() -> None:
    pkg = 'my'
    subpackages = find_namespace_packages('.', include=('my.*',))
    setup(
        use_scm_version={
            # todo eh? not sure if I should just rely on proper tag naming and use use_scm_version=True
            # 'version_scheme': 'python-simplified-semver',
            'local_scheme': 'dirty-tag',
        },
        zip_safe=False,
        # eh. find_packages doesn't find anything
        # find_namespace_packages can't find single file packages (like my/common.py)
        packages=[pkg, *subpackages],
        package_data={
            pkg: [
                # for mypy
                'py.typed',
            ],
        },
        url='https://github.com/karlicoss/HPI',
    )


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--dependencies-only', action='store_true')
    args, _ = p.parse_known_args()
    if args.dependencies_only:
        cmd = ['pip3', 'install', '--user', *INSTALL_REQUIRES]
        scmd = ' '.join(cmd)
        import os
        xx = input(f'Run {scmd} [y/n] ')
        if xx.strip() == 'y':
            os.execvp(
                'pip3',
                cmd
            )
    else:
        main()

# TODO assert??? diff -bur my/ ~/.local/lib/python3.8/site-packages/my/
