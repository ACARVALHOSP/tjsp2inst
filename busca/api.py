from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from cjsg import Acordao, CJSGClient, ResultadoBusca

app = FastAPI(
    title="Busca Jurisprudência TJSP",
    description="Busca acórdãos do TJSP via CJSG",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/busca", response_model=dict)
def buscar(
    q: str = Query(..., description="Palavras-chave (busca no texto da ementa)"),
    data_ini: Optional[str] = Query(None, description="Data início julgamento YYYY-MM-DD"),
    data_fim: Optional[str] = Query(None, description="Data fim julgamento YYYY-MM-DD"),
    pagina: int = Query(1, ge=1, description="Página de resultados"),
    max_paginas: int = Query(1, ge=1, le=10, description="Quantas páginas buscar"),
):
    """
    Busca acórdãos no CJSG do TJSP.

    Retorna metadados e ementa de cada acórdão encontrado.
    """
    try:
        with CJSGClient() as client:
            if max_paginas == 1:
                resultado = client.buscar(
                    livre=q,
                    data_ini_julg=data_ini,
                    data_fim_julg=data_fim,
                    pagina=pagina,
                )
            else:
                resultado = client.buscar_todas_paginas(
                    livre=q,
                    data_ini_julg=data_ini,
                    data_fim_julg=data_fim,
                    max_paginas=max_paginas,
                )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Erro ao consultar TJSP: {e}")

    return {
        "total": resultado.total,
        "pagina": resultado.pagina,
        "total_paginas": resultado.total_paginas,
        "n_retornados": len(resultado.acordaos),
        "acordaos": [_acordao_dict(a) for a in resultado.acordaos],
    }


def _acordao_dict(a: Acordao) -> dict:
    return {
        "cd_acordao": a.cd_acordao,
        "n_processo": a.n_processo,
        "classe_assunto": a.classe_assunto,
        "comarca": a.comarca,
        "orgao_julgador": a.orgao_julgador,
        "relator": a.relator,
        "data_julgamento": a.data_julgamento,
        "data_registro": a.data_registro,
        "outros_numeros": a.outros_numeros,
        "ementa": a.ementa,
    }
