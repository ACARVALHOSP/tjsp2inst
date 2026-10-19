import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup

BASE_URL = "http://esaj.tjsp.jus.br/cjsg"
RECAPTCHA_SITE_KEY = "6LcXJIAbAAAAAOwprTGEEYwRSe-HMYD-Ys0pSR6f"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "http://esaj.tjsp.jus.br/cjsg/resultadoCompleta.do",
    "Origin": "http://esaj.tjsp.jus.br",
}
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


class CJSGClient:
    def __init__(self):
        self._client = httpx.Client(headers=HEADERS, follow_redirects=True, timeout=30)
        self._form_base: dict = {}

    def _get_recaptcha_token(self) -> str:
        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import stealth_sync
        except ImportError:
            return ""

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="pt-BR",
                )
                page = context.new_page()
                stealth_sync(page)
                page.goto(
                    f"{BASE_URL}/resultadoCompleta.do",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_function(
                    "() => typeof window.grecaptcha !== 'undefined'",
                    timeout=15000,
                )
                token = page.evaluate(f"""
                    () => new Promise((resolve) => {{
                        grecaptcha.enterprise.ready(() => {{
                            grecaptcha.enterprise
                                .execute('{RECAPTCHA_SITE_KEY}', {{action: 'consulta'}})
                                .then(resolve)
                                .catch(() => resolve(''));
                        }});
                    }})
                """)
                return token or ""
            finally:
                browser.close()

    def _abrir_sessao(self):
        r = self._client.get(f"{BASE_URL}/resultadoCompleta.do")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form")
        self._form_base = {}
        if form:
            for inp in form.find_all(["input", "select", "textarea"]):
                name = inp.get("name")
                if name:
                    self._form_base[name] = inp.get("value", "")
        time.sleep(DELAY)

    def _n_total(self, soup: BeautifulSoup) -> int:
        el = soup.select_one("#nomeAba-A")
        if not el:
            return 0
        texto = re.sub(r"[^0-9]", "", el.text)
        return int(texto) if texto else 0

    def _parse_pagina(self, html: str) -> list[Acordao]:
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

    def buscar(
        self,
        livre: str = "",
        data_ini_julg: Optional[str] = None,
        data_fim_julg: Optional[str] = None,
        pagina: int = 1,
    ) -> ResultadoBusca:
        """Busca acórdãos no CJSG. Datas no formato YYYY-MM-DD."""
        if not self._form_base:
            self._abrir_sessao()

        if pagina == 1:
            dados = dict(self._form_base)
            dados["dados.buscaInteiroTeor"] = livre
            dados["dados.dtJulgamentoInicio"] = _fmt_data(data_ini_julg) or ""
            dados["dados.dtJulgamentoFim"] = _fmt_data(data_fim_julg) or ""
            dados["dados.pesquisarComSinonimos"] = "S"
            dados["tipoDecisaoSelecionados"] = "A"
            dados["recaptcha_response_token"] = self._get_recaptcha_token()

            r = self._client.post(f"{BASE_URL}/resultadoCompleta.do", data=dados)
        else:
            time.sleep(DELAY)
            r = self._client.get(
                f"{BASE_URL}/trocaDePagina.do",
                params={"tipoDeDecisao": "A", "pagina": pagina},
            )

        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        total = self._n_total(soup)
        total_paginas = (total // 20) + (1 if total % 20 else 0)

        return ResultadoBusca(
            total=total,
            pagina=pagina,
            total_paginas=total_paginas,
            acordaos=self._parse_pagina(r.text),
        )

    def buscar_todas_paginas(self, max_paginas: int = 5, **kwargs) -> ResultadoBusca:
        resultado = self.buscar(pagina=1, **kwargs)
        limite = min(max_paginas, resultado.total_paginas)
        for pag in range(2, limite + 1):
            time.sleep(DELAY)
            r = self.buscar(pagina=pag, **kwargs)
            resultado.acordaos.extend(r.acordaos)
        return resultado

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _fmt_data(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    try:
        ano, mes, dia = d.split("-")
        return f"{dia}/{mes}/{ano}"
    except ValueError:
        return d
