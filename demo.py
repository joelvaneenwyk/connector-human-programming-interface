#!/usr/bin/env python3
import os
from contextlib import contextmanager

from pathlib import Path
from shutil import copytree, ignore_patterns, rmtree
from subprocess import DEVNULL, check_call
from sys import executable as python
import tempfile

my_repo = Path(__file__).absolute().parent


def run() -> None:
    """
    running should result in something like this:

    URL:   https://tacticaltech.org/
    Title: Tactical Technology Collective
    1 annotations

    URL:   https://web.hypothes.is/blog/annotating-the-wild-west-of-information-flow/
    Title: Annotating the wild west of information flow - Hypothesis
    1 annotations

    URL:   http://www.liberation.fr/futurs/2016/12/12/megafichier-beauvau-prie-de-revoir-sa-copie_1534720
    Title: «Mégafichier»: Beauvau prié de revoir sa copie
    3 annotations

    URL:   https://www.wired.com/2016/12/7500-faceless-coders-paid-bitcoin-built-hedge-funds-brain/
    Title: 7,500 Faceless Coders Paid in Bitcoin Built a Hedge Fund's Brain
    4 annotations

    URL:   http://realscreen.com/2016/12/06/project-x-tough-among-sundance-17-doc-shorts/
    Title: “Project X,” “Tough” among Sundance '17 doc shorts
    1 annotations

    URL:   https://grehack.fr/2016/program
    Title: GreHack | Security conference and hacking game 2016 | Grenoble
    1 annotations

    URL:   https://respectmynet.eu/
    Title: [!] Respect My Net
    1 annotations

    URL:   https://www.youtube.com/watch?v=Xgp7BIBtPhk
    Title: BBC Documentaries 2016: The Joy of Data [FULL BBC SCIENCE DOCUMENTARY]
    1 annotations
    """

    # uses fixed paths; worth it for the sake of demonstration
    # assumes we're in /tmp/my_demo now

    # 1. clone git@github.com:karlicoss/my.git
    copytree(
        my_repo,
        'my_repo',
        symlinks=True,
        ignore=ignore_patterns('.tox*'),  # tox dir might have broken symlinks while tests are running in parallel
    )

    # 2. prepare repositories you'd be using. For this demo we only set up Hypothesis
    try:
        import hypexport  # type: ignore  # pylint: disable=unused-import,import-outside-toplevel
    except ModuleNotFoundError:
        # tox doesn't like --user flag
        check_call(f'{python} -m pip {"" if 'TOX' in os.environ else "--user"} git+https://github.com/karlicoss/hypexport.git'.split())

    # 3. prepare some demo Hypothesis data
    hypothesis_backups = Path('backups/hypothesis').resolve()
    hypothesis_backups.mkdir(exist_ok=True, parents=True)
    check_call([
        'curl',
        'https://raw.githubusercontent.com/taniki/netrights-dashboard-mockup/master/_data/annotations.json',
        '-o', f'{hypothesis_backups}/annotations.json',
    ], stderr=DEVNULL)
    #

    # 4. point my.config to the Hypothesis data
    mycfg_root = Path('my_repo').resolve()
    mycfg_root.mkdir(exist_ok=True, parents=True)
    init_file: Path = mycfg_root / 'my/config.py'
    init_file.write_text(init_file.read_text().replace(
        '/path/to/hypothesis/data',
        str(hypothesis_backups),
    ))
    #

    # 4. now we can use it!
    os.chdir(my_repo)

    check_call([python, '-c', '''
import my.hypothesis

pages = my.hypothesis.pages()

from itertools import islice
for page in islice(pages, 0, 8):
    print('URL:   ' + page.url)
    print('Title: ' + page.title)
    print('{} annotations'.format(len(page.highlights)))
    print()
'''], env={
    # this is just to prevent demo.py from using real data
    # normally, it will rely on having my.config in ~/.config/my
    'MY_CONFIG': str(mycfg_root),
    **os.environ,
})


@contextmanager
def named_temp_dir(name: str):
    """
    Create a unique temporary directory and return a path that includes the specified name.
    """
    unique_temp_dir = Path(tempfile.mkdtemp())
    td = unique_temp_dir / name
    try:
        td.mkdir()
        yield td
    except OSError as e:
        print(f"Failed to create {unique_temp_dir}: {e}")
    finally:
        try:
            rmtree(str(unique_temp_dir), ignore_errors=True)
        except OSError as e:
            print(f"Failed to cleanup {unique_temp_dir}: {e}")


def main():
    """Create a temporary directory and run the demo."""
    with named_temp_dir('my_demo') as tdir:
        os.chdir(tdir)
        run()


if __name__ == '__main__':
    main()
