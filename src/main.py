import logging
import re
import requests_cache

from bs4 import BeautifulSoup
from collections import defaultdict
from tqdm import tqdm
from urllib.parse import urljoin

from configs import configure_argument_parser, configure_logging
from constants import (
    BASE_DIR, MAIN_DOC_URL,
    BASE_URL, PEP_URL,
    EXPECTED_STATUS
)
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
    sections_by_python = div_with_ul.find_all(
        'li',
        attrs={'class': 'toctree-l1'}
    )
    results = [('Ссылка на статью', 'Заголовок', 'Редактор, автор')]
    for section in tqdm(sections_by_python):
        version_a_tag = find_tag(section, 'a')
        if version_a_tag is None:
            continue
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
    sidebar = soup.find_tag(
        soup,
        'div',
        attrs={'class': 'sphinxsidebarwrapper'}
    )
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
    pdf_a4_tag = find_tag(
        table_tag,
        'a',
        attrs={'href': re.compile(r'.+pdf-a4\.zip$')}
    )
    archive_url = urljoin(downloads_url, pdf_a4_tag['href'])
    downloads_dir = BASE_DIR / 'downloads'
    downloads_dir.mkdir(exist_ok=True)
    filename = archive_url.split('/')[-1]
    archive_path = downloads_dir / filename
    response = get_response(session, archive_url)
    if response is None:
        logging.error(f'Не удалось загрузить архив: {archive_url}')
        return
    with open(archive_path, 'wb') as file:
        file.write(response.content)
        logging.info(f'Архив был загружен и сохранён: {archive_path}')


def parse_pep_table(soup):
    """Парсинг таблицы PEP."""
    peps = []
    tables = soup.find_all(
        'table',
        class_='pep-zero-table docutils align-default'
    )
    for table in tables:
        tbody = table.find('tbody')
        if not tbody:
            continue
        rows = tbody.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 2:
                continue

            abbr = cols[0].find('abbr')
            if abbr:
                preview_status = abbr.text.strip()[1:]
            else:
                preview_status = ''

            link_tag = cols[1].find('a', class_='pep reference internal')
            if not link_tag:
                continue
            href = link_tag['href']
            pep_number = cols[1].text.strip()
            if pep_number == '0':
                continue

            pep_link = urljoin(BASE_URL, href)
            peps.append({
                'number': pep_number,
                'expected_status_code': preview_status,
                'url': pep_link
            })
    return peps


def extract_pep_status(html):
    """Извлечение статуса со страницы PEP."""
    soup = BeautifulSoup(html, 'html.parser')

    status_block = soup.find('div', class_='status')
    if status_block:
        strong_tag = status_block.find('strong')
        if strong_tag:
            return strong_tag.text.strip()

    status_block = soup.find('dl', class_='rfc2822 field-list simple')
    if status_block:
        for dt in status_block.find_all('dt'):
            if 'Status' in dt.text:
                status_dd = dt.find_next_sibling('dd')
                if status_dd:
                    abbr = status_dd.find('abbr')
                    if abbr:
                        return abbr.text.strip()
                    return status_dd.text.strip()
    return 'Not Found'


def pep(session):
    """Основная функция парсинга."""
    response = get_response(session, PEP_URL)
    if not response:
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    peps = parse_pep_table(soup)

    status_counts = defaultdict(int)
    mismatches = []

    for pep_item in tqdm(peps, desc='Processing PEPs'):
        expected_status_code = pep_item['expected_status_code']
        expected_statuses = EXPECTED_STATUS.get(
            expected_status_code.split(',')[0].strip(), ('Unknown',)
        )
        pep_response = get_response(session, pep_item['url'])
        if not pep_response:
            real_status = 'Not Found'
        else:
            real_status = extract_pep_status(pep_response.text)
        status_counts[real_status] += 1

        if real_status not in expected_statuses and real_status != 'Not Found':
            mismatches.append({
                'url': pep_item['url'],
                'real_status': real_status,
                'expected_statuses': list(expected_statuses)
            })

    if mismatches:
        error_messages = [
            f"Несовпадающие статусы:\n"
            f"URL: {m['url']}\n"
            f"Статус на странице: {m['real_status']}\n"
            f"Ожидаемые статусы: {m['expected_statuses']}"
            for m in mismatches
        ]
        logging.warning('\n\n'.join(error_messages))

    results = [['Статус', 'Количество']]
    for status, count in sorted(status_counts.items()):
        results.append([status, count])
    results.append(['Total', sum(status_counts.values())])

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
