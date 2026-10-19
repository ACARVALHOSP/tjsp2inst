import re
import time
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page
from playwright_stealth import stealth_sync

BASE_URL = "http://esaj.tjsp.jus.br/cjsg/resultadoCompleta.do"
DELAY = 1.5


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


import os

# caminho padrão do perfil do Chrome no Windows
_CHROME_PROFILE_WIN = os.path.expandvars(
    r"%LOCALAPPDATA%\Google\Chrome\User Data"
)


class CJSGClient:
    def __init__(self, headless: bool = False, chrome_profile: str | None = None):
        """
        headless: False recomendado para evitar bloqueio por reCAPTCHA.
        chrome_profile: caminho para o User Data do Chrome. Se informado,
            usa o perfil real do usuário (mais confiável com reCAPTCHA).
            Padrão: tenta o perfil do Chrome no Windows automaticamente.
        """
        self._headless = headless
        self._chrome_profile = chrome_profile or (
            _CHROME_PROFILE_WIN if os.path.exists(_CHROME_PROFILE_WIN) else None
        )
        self._pw = None
        self._context = None
        self._page = None

    def _iniciar(self):
        self._pw = sync_playwright().start()

        if self._chrome_profile and os.path.exists(self._chrome_profile):
            # usa o Chrome real com o perfil do usuário
            # o Chrome precisa estar fechado antes de rodar
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=self._chrome_profile,
                channel="chrome",
                headless=self._headless,
                args=["--profile-directory=Default"],
            )
            self._page = self._context.new_page()
        else:
            # fallback: Chromium com stealth
            browser = self._pw.chromium.launch(headless=self._headless)
            self._context = browser.new_context()
            self._page = self._context.new_page()
            stealth_sync(self._page)

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
            # usa seletor por name para evitar problemas com pontos no ID
            page.fill('[name="dados.buscaInteiroTeor"]', livre)

            if data_ini_julg:
                page.fill('[name="dados.dtJulgamentoInicio"]', _fmt_data(data_ini_julg))
            if data_fim_julg:
                page.fill('[name="dados.dtJulgamentoFim"]', _fmt_data(data_fim_julg))

            # submete o form (o reCAPTCHA é resolvido automaticamente pelo browser)
            page.click('[name="pbSubmit"]')
            _aguardar_resultados(page)
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
        if self._context:
            self._context.close()
        if self._pw:
            self._pw.stop()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
