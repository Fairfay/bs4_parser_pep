from urllib.parse import urljoin
from constants import (
    BASE_DIR, MAIN_DOC_URL,
    BASE_URL, PEP_LIST_URL,
    EXPECTED_STATUS
)
import re
import logging
import requests_cache
from bs4 import BeautifulSoup
from tqdm import tqdm
from pathlib import Path
from configs import configure_argument_parser, configure_logging
from outputs import control_output
from utils import get_response, find_tag

def whats_new(session):
    whats_new_url = urljoin(MAIN_DOC_URL, 'whatsnew/')
    response = get_response(session, whats_new_url)
    if response is None:
        return
    soup = BeautifulSoup(response.text, features='lxml')
    main_div = find_tag(soup, 'section', attrs={'id': 'what-s-new-in-python'})
    div_with_ul = find_tag(main_div, 'div', attrs={'class': 'toctree-wrapper'})
    sections_by_python = div_with_ul.find_all('li', attrs={'class': 'toctree-l1'})
    results = [('Ссылка на статью', 'Заголовок', 'Редактор, автор')]
    for section in tqdm(sections_by_python):
        version_a_tag = section.find('a')
        version_link = urljoin(whats_new_url, version_a_tag['href'])
        response = get_response(session, version_link)
        if response is None:
            continue
        soup = BeautifulSoup(response.text, features='lxml')
        h1 = find_tag(soup, 'h1')
        dl = find_tag(soup, 'dl')
        dl_text = dl.text.replace('\n', ' ')
        results.append(
            (version_link, h1.text, dl_text)
        )
    return results 

def latest_versions(session):
    response = get_response(session, MAIN_DOC_URL)
    if response is None:
        return
    soup = BeautifulSoup(response.text, 'lxml')
    sidebar = soup.find_tag(soup, 'div', attrs={'class': 'sphinxsidebarwrapper'})
    ul_tags = sidebar.find_all('ul')
    for ul in ul_tags:
        if 'All versions' in ul.text:
            a_tags = ul.find_all('a')
            break
    else:
        raise Exception('Не найден список c версиями Python')
    results = [('Ссылка на документацию', 'Версия', 'Статус')]
    pattern = r'Python (?P<version>\d\.\d+) \((?P<status>.*)\)'
    for a_tag in a_tags:
        link = a_tag['href']
        text_match = re.search(pattern, a_tag.text)
        if text_match is not None:  
            version, status = text_match.groups()
        else:  
            version, status = a_tag.text, ''  
        results.append(
            (link, version, status)
        )
    return results

def download(session):
    downloads_url = urljoin(MAIN_DOC_URL, 'download.html')
    response = get_response(session, downloads_url)
    if response is None:
        return
    soup = BeautifulSoup(response.text, 'lxml')
    main_tag = find_tag(soup, 'div', attrs={'role': 'main'})
    table_tag = find_tag(main_tag, 'table', attrs={'class': 'docutils'})
    pdf_a4_tag = find_tag(table_tag, 'a', attrs={'href': re.compile(r'.+pdf-a4\.zip$')})
    archive_url = urljoin(downloads_url, pdf_a4_tag['href'])
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    filename = archive_url.split('/')[-1]
    archive_path = downloads_dir / filename
    response = session.get(archive_url)
    with open(archive_path, 'wb') as file:
        file.write(response.content)
        logging.info(f'Архив был загружен и сохранён: {archive_path}')

def pep(session):
    response = get_response(session, PEP_LIST_URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    table = soup.find('table')
    rows = table.find_all('tr')[1:]

    pebs = []
    for row in rows:
        cols = row.find_all('td')
        if len(cols) < 2:
            continue
        code = cols[0].text.strip()
        pep_num = cols[1].text.strip()
        if pep_num == '0':
            continue
        pebs.append({
            'number': pep_num,
            'expected_status_code': code[-1] if code else '',
            'url': urljoin(BASE_URL, f'/pep-{pep_num.zfill(4)}')
        })

    counts = {}
    mismatches = []

    for pep in tqdm(pebs, desc='Processing PEPs'):
        expected_status_code = pep['expected_status_code']
        expected_statuses = EXPECTED_STATUS.get(expected_status_code, ('Unknown',))
        pep_response = get_response(session, pep['url'])
        real_status = None
        if pep_response is not None:
            pep_soup = BeautifulSoup(pep_response.text, 'html.parser')
            status_tag = pep_soup.find('dt', string='Status')
            if status_tag:
                status_value = status_tag.find_next_sibling('dd')
                if status_value:
                    real_status = status_value.text.strip()
        if not real_status:
            real_status = 'Not Found'
        counts[real_status] = counts.get(real_status, 0) + 1

        if real_status not in expected_statuses and real_status != 'Not Found':
            mismatches.append({
                'url': pep['url'],
                'real_status': real_status,
                'expected_statuses': list(expected_statuses)
            })
            logging.warning(
                f"Несовпадающие статусы: {pep['url']}\n"
                f"Статус в карточке: {real_status}\n"
                f"Ожидаемые статусы: {list(expected_statuses)}\n"
            )

    total = sum(counts.values())
    results = [['Статус', 'Количество']]
    for status, count in sorted(counts.items()):
        results.append([status, count])
    results.append(['Total', total])
    return results

MODE_TO_FUNCTION = {
    'pep': pep,
    'whats-new': whats_new,
    'latest-versions': latest_versions,
    'download': download
}


def main():
    configure_logging()
    logging.info('Парсер запущен!')

    arg_parser = configure_argument_parser(MODE_TO_FUNCTION.keys())
    args = arg_parser.parse_args()
    logging.info(f'Аргументы командной строки: {args}')

    session = requests_cache.CachedSession()
    if args.clear_cache:
        session.cache.clear()

    parser_mode = args.mode
    results = MODE_TO_FUNCTION[parser_mode](session)

    if results is not None:
        control_output(results, args)
    logging.info('Парсер завершил работу.') 

if __name__ == '__main__':
    main() 