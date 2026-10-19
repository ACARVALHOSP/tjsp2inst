import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page

BASE_URL = "http://esaj.tjsp.jus.br/cjsg/resultadoCompleta.do"
DELAY = 1.5
CDP_PORT = 9222
CHROME_WIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_WIN_X86 = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"


@dataclass
class Acordao:
    cd_acordao: str
    n_processo: str
    classe_assunto: str
    comarca: str
    orgao_julgador: str
    relator: str
    data_julgamento: str
    data_registro: str
    ementa: str
    outros_numeros: str = ""


@dataclass
class ResultadoBusca:
    total: int
    pagina: int
    total_paginas: int
    acordaos: list[Acordao] = field(default_factory=list)


def _n_total(soup: BeautifulSoup) -> int:
    el = soup.select_one("#nomeAba-A")
    if not el:
        return 0
    texto = re.sub(r"[^0-9]", "", el.text)
    return int(texto) if texto else 0


def _parse_pagina(html: str) -> list[Acordao]:
    soup = BeautifulSoup(html, "html.parser")
    acordaos = []

    for item in soup.select(".fundocinza1"):
        try:
            cd_acordao = ""
            n_processo = ""
            link = item.select_one(".downloadEmenta")
            if link:
                n_processo = link.text.strip()
                cd_acordao = link.get("cdacordao", "")

            classe_assunto = ""
            ca = item.select_one(".assuntoClasse")
            if ca:
                classe_assunto = ca.text.strip()

            ementa = ""
            ta = item.select_one("textarea")
            if ta:
                ementa = ta.text.strip()

            campos = {}
            for el in item.select(".ementaClass2"):
                partes = el.text.split(":", 1)
                if len(partes) == 2:
                    chave = partes[0].strip().lower()
                    chave = re.sub(r"\s+", "_", chave)
                    chave = re.sub(r"[^a-z_]", "", chave)
                    campos[chave] = partes[1].strip()

            acordaos.append(Acordao(
                cd_acordao=cd_acordao,
                n_processo=n_processo,
                classe_assunto=classe_assunto,
                comarca=campos.get("comarca", ""),
                orgao_julgador=campos.get("orgao_julgador", ""),
                relator=campos.get("relatora", campos.get("relator", "")),
                data_julgamento=campos.get("data_do_julgamento", ""),
                data_registro=campos.get("data_de_registro", ""),
                ementa=ementa,
                outros_numeros=campos.get("outros_numeros", ""),
            ))
        except Exception:
            continue

    return acordaos


def _fmt_data(d: Optional[str]) -> str:
    """Converte YYYY-MM-DD para DD/MM/YYYY."""
    if not d:
        return ""
    try:
        ano, mes, dia = d.split("-")
        return f"{dia}/{mes}/{ano}"
    except ValueError:
        return d


def _aguardar_resultados(page: Page, timeout: int = 15000):
    """Aguarda a página de resultados carregar."""
    try:
        page.wait_for_selector(".fundocinza1", timeout=timeout)
    except Exception:
        pass  # pode não ter resultados


def _chrome_exe() -> str:
    for path in [CHROME_WIN, CHROME_WIN_X86]:
        if os.path.exists(path):
            return path
    raise FileNotFoundError("Chrome não encontrado. Informe o caminho via chrome_exe=")


class CJSGClient:
    def __init__(self, chrome_exe: str | None = None, user_data_dir: str | None = None):
        """
        Lança o Chrome real sem o flag --enable-automation, que é detectado
        pelo reCAPTCHA. O Playwright conecta ao Chrome via CDP.

        chrome_exe: caminho para o chrome.exe (detectado automaticamente no Windows).
        user_data_dir: pasta de perfil temporária. Um perfil novo é criado em
            %TEMP%\\chrome-tjsp se não for informado.
        """
        self._chrome_exe = chrome_exe or _chrome_exe()
        self._user_data_dir = user_data_dir or os.path.join(
            os.environ.get("TEMP", "/tmp"), "chrome-tjsp"
        )
        self._proc: subprocess.Popen | None = None
        self._pw = None
        self._browser = None
        self._page = None

    def _iniciar(self):
        os.makedirs(self._user_data_dir, exist_ok=True)

        # lança Chrome sem --enable-automation
        self._proc = subprocess.Popen([
            self._chrome_exe,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={self._user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ])
        time.sleep(2)

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = context.new_page() if not context.pages else context.pages[0]

        self._page.goto(BASE_URL)
        self._page.wait_for_selector("form", timeout=15000)

    def buscar(
        self,
        livre: str = "",
        data_ini_julg: Optional[str] = None,
        data_fim_julg: Optional[str] = None,
        pagina: int = 1,
    ) -> ResultadoBusca:
        if self._page is None:
            self._iniciar()

        page = self._page

        if pagina == 1:
            # preenche os campos visíveis
            page.fill('[name="dados.buscaInteiroTeor"]', livre)

            if data_ini_julg:
                page.fill('[name="dados.dtJulgamentoInicio"]', _fmt_data(data_ini_julg))
            if data_fim_julg:
                page.fill('[name="dados.dtJulgamentoFim"]', _fmt_data(data_fim_julg))

            print("\n" + "="*60)
            print("O formulário foi preenchido no Chrome.")
            print("Clique em 'Pesquisar' no browser e aguarde os resultados.")
            print("Quando os resultados aparecerem, pressione ENTER aqui.")
            print("="*60 + "\n")
            input()
            _aguardar_resultados(page, timeout=5000)
        else:
            # navega para a página N
            page.goto(
                f"http://esaj.tjsp.jus.br/cjsg/trocaDePagina.do?tipoDeDecisao=A&pagina={pagina}"
            )
            _aguardar_resultados(page)

        time.sleep(DELAY)
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")
        total = _n_total(soup)
        total_paginas = (total // 20) + (1 if total % 20 else 0)
        acordaos = _parse_pagina(html)

        return ResultadoBusca(
            total=total,
            pagina=pagina,
            total_paginas=total_paginas,
            acordaos=acordaos,
        )

    def buscar_todas_paginas(
        self,
        max_paginas: int = 5,
        **kwargs,
    ) -> ResultadoBusca:
        resultado = self.buscar(pagina=1, **kwargs)
        limite = min(max_paginas, resultado.total_paginas)

        for pag in range(2, limite + 1):
            time.sleep(DELAY)
            r = self.buscar(pagina=pag, **kwargs)
            resultado.acordaos.extend(r.acordaos)

        return resultado

    def close(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
        if self._proc:
            self._proc.terminate()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
