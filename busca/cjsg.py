import time
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup

BASE_URL = "http://esaj.tjsp.jus.br/cjsg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}
DELAY = 1.5  # segundos entre requisições


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
        self._sessao_aberta = False

    def _abrir_sessao(self):
        """Abre sessão no CJSG e obtém o cookie necessário."""
        self._client.get(f"{BASE_URL}/resultadoCompleta.do")
        self._sessao_aberta = True
        time.sleep(DELAY)

    def _n_total(self, soup: BeautifulSoup) -> int:
        el = soup.select_one("#nomeAba-A")
        if not el:
            return 0
        return int(re.sub(r"[^0-9]", "", el.text) or "0")

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
        data_ini_reg: Optional[str] = None,
        data_fim_reg: Optional[str] = None,
        secoes: str = "",
        classes: str = "",
        assuntos: str = "",
        pagina: int = 1,
    ) -> ResultadoBusca:
        """
        Busca acórdãos no CJSG.

        Datas no formato DD/MM/YYYY.
        Retorna metadados + texto da ementa de cada acórdão.
        """
        if not self._sessao_aberta:
            self._abrir_sessao()

        # primeira página: submete o formulário
        if pagina == 1:
            dados = {
                "dados.buscaInteiroTeor": livre,
                "dados.dtJulgamentoInicio": _fmt_data(data_ini_julg) or "",
                "dados.dtJulgamentoFim": _fmt_data(data_fim_julg) or "",
                "dados.dtRegistroInicio": _fmt_data(data_ini_reg) or "",
                "dados.dtRegistroFim": _fmt_data(data_fim_reg) or "",
                "secoesTreeSelection.values": secoes,
                "classesTreeSelection.values": classes,
                "assuntosTreeSelection.values": assuntos,
                "dados.pesquisarComSinonimos": "true",
            }
            r = self._client.post(
                f"{BASE_URL}/resultadoCompleta.do",
                data=dados,
            )
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
        acordaos = self._parse_pagina(r.text)

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
        """Busca e agrega múltiplas páginas."""
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
    """Converte YYYY-MM-DD para DD/MM/YYYY."""
    if not d:
        return None
    try:
        ano, mes, dia = d.split("-")
        return f"{dia}/{mes}/{ano}"
    except ValueError:
        return d
