"""
Gerador de Insights Semanais de KPIs usando API OpenAI (Agrupamento + Lote)

Este script realiza a consolidação executiva de indicadores de desempenho (KPIs) 
de um e-commerce de moda. Ele lê uma base relacional contendo métricas regionais por semana, 
agrupa os dados em memória para gerar um briefing executivo consolidado e envia o contexto
para a OpenAI via API Responses com Structured Outputs.

DIFERENCIAIS TÉCNICOS:
  - Consolidação Agregada em Memória: Transforma múltiplos registros regionais em um 
    resumo semanal estruturado (visão macro).
  - Saída Estruturada (Structured Outputs / JSON Schema): Garante o retorno estrito 
    do insight no formato JSON esperado.
  - Idempotência e Evitação de Retrabalho: Detecta análises já existentes no arquivo de 
    destino e processa apenas semanas pendentes.
  - Gravação Atômica: Garante a escrita segura no disco via arquivo temporário.

REQUISITOS:
  - Variável de ambiente OPENAI_API_KEY configurada.
  - Arquivo CSV contendo as colunas: 'semana', 'regiao', 'receita_liquida', 'meta_receita',
    'ticket_medio', 'taxa_devolucao_pct', 'nps' e 'var_semana_anterior_pct'.

EXEMPLO DE USO:
  python gerar_insights_semanais.py "kpis_semanais.csv"
"""

import argparse
import csv
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ============================================================================
# CONFIGURAÇÕES E CONSTANTES CONFIGURÁVEIS
# ============================================================================

# Número máximo de tentativas de reenvio em caso de falha temporária na API
MAX_TENTATIVAS = 5

# Tempo limite máximo (em segundos) de espera por uma resposta da rede
TIMEOUT = 60

# Diretrizes do sistema (System Prompt) orientando o modelo de linguagem sobre
# a postura analítica executiva, limitações de tamanho e estilo de escrita.
INSTRUCOES = """Você é um Diretor de Business Intelligence e Analytics sênior.
Sua tarefa é analisar os KPIs consolidados da semana de um e-commerce de moda e gerar um insight executivo.

Para a semana fornecida, você receberá os dados consolidados:
- Receita Líquida Total vs Meta Total
- Comparativo com a Semana Anterior
- Desempenho Regional detalhado
- Médias de Ticket Médio, Taxa de Devolução e NPS

Escreva um parágrafo narrativo ultra-conciso (de no máximo 2 a 3 linhas), focando estritamente nos números mais críticos e na conclusão. Evite redundâncias textuais.
Seu texto deve:
1. Apontar o principal driver do resultado (o que causou a alta ou a baixa).
2. Destacar anomalias críticas (ex: quedas severas em regiões específicas, NPS baixo ou devoluções altas).
3. Avaliar o NPS na escala de -100 a +100: valores abaixo de 0 são Críticos, de 0 a 50 indicam Alerta/Aperfeiçoamento, de 51 a 75 são considerados Bons/Qualidade e acima de 75 Excelentes. Não trate um NPS >= 51 como indicador de insatisfação.
4. Ser direto, analítico e profissional, sem clichês comerciais.

Responda exclusivamente no formato JSON exigido pelo schema, sem markdown ou explicações externas."""

# Definição formal do esquema JSON (JSON Schema) para a funcionalidade Structured Outputs.
# Força a API a responder um objeto com a chave "insight".
JSON_SCHEMA = {
    "name": "insight_semanal",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "insight": {
                "type": "string"
            }
        },
        "required": ["insight"],
        "additionalProperties": False
    }
}

# ============================================================================
# FUNÇÕES DE SUPORTE, CONVERSÃO E LEITURA
# ============================================================================

def formatar_moeda(valor):
    """
    Converte um valor numérico float para uma string formatada no padrão monetário brasileiro (R$).
    Exemplo: 790724.32 -> "R$ 790.724,32"
    """
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def converter_float(valor):
    """
    Converte com segurança valores de texto formatados em pt-BR (vírgula decimal)
    ou en-US (ponto decimal) para o tipo primitivo float em Python.
    """
    if not valor or str(valor).strip() == "":
        return 0.0
    try:
        # Trata formatos pt-BR (ex: 790724,32 -> 790724.32)
        limpo = str(valor).replace(".", "").replace(",", ".")
        return float(limpo)
    except ValueError:
        return 0.0

def extrair_insight_da_resposta(resposta_json):
    """
    Navega pela estrutura de resposta retornada pela API Responses da OpenAI 
    para extrair a string contendo o insight analítico.
    
    Suporta saídas com marcadores de bloco de código Markdown ou JSON puro.
    
    Retorna:
        str: O texto do insight ou mensagem descritiva de erro em caso de falha de parsing.
    """
    try:
        for item in resposta_json.get("output", []):
            if item.get("type") == "message":
                for conteudo in item.get("content", []):
                    if conteudo.get("type") == "output_text":
                        texto = conteudo.get("text", "").strip()
                        if texto.startswith("```json"):
                            texto = texto.split("```json", 1)[1].rsplit("```", 1)[0].strip()
                        elif texto.startswith("```"):
                            texto = texto.split("```", 1)[1].rsplit("```", 1)[0].strip()
                        dados = json.loads(texto)
                        return dados.get("insight", "Insight não gerado corretamente no JSON.")
        return "Erro: Estrutura de resposta inesperada da API."
    except Exception as e:
        return f"Erro ao processar o insight: {e}"

def requisitar_insight(briefing_texto, chave_api, modelo):
    """
    Prepara e envia a requisição HTTP POST para a API Responses da OpenAI contendo
    o briefing semanal consolidado e retorna o insight gerado.
    
    Inclui mecanismo de retentativa automática (retry) em caso de limite de taxa (429) ou erro de rede.
    """
    input_estruturado = [{
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": briefing_texto}]
    }]

    # Monta o payload no formato exigido pela API Responses com JSON Schema ativado
    corpo = json.dumps({
        "model": modelo,
        "instructions": INSTRUCOES,
        "input": input_estruturado,
        "max_output_tokens": 350,
        "temperature": 0.2,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "insight_semanal",
                "strict": True,
                "schema": JSON_SCHEMA["schema"]
            }
        }
    }).encode("utf-8")

    requisicao = Request(
        "https://api.openai.com/v1/responses",
        data=corpo,
        headers={"Authorization": f"Bearer {chave_api}", "Content-Type": "application/json"},
        method="POST"
    )

    # Loop de tentativas com política de retentativa e backoff exponencial
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            with urlopen(requisicao, timeout=TIMEOUT) as resposta_http:
                dados_resposta = json.loads(resposta_http.read().decode("utf-8"))
                return extrair_insight_da_resposta(dados_resposta)
        except HTTPError as erro:
            if erro.code == 429 and tentativa < MAX_TENTATIVAS:
                espera = 2 ** tentativa
                time.sleep(espera)
                continue
            raise RuntimeError(f"Erro HTTP {erro.code} na API OpenAI.")
        except (URLError, json.JSONDecodeError) as e:
            if tentativa == MAX_TENTATIVAS:
                raise RuntimeError(f"Falha de conexão persistente: {e}")
            time.sleep(2 ** tentativa)

# ============================================================================
# LÓGICA PRINCIPAL DE AGRUPAMENTO, AGRAGAÇÃO E EXECUÇÃO
# ============================================================================

def main():
    # Interface de Linha de Comando (CLI) para parsing dos argumentos
    parser = argparse.ArgumentParser(description="Consolida KPIs por semana e gera Insights executivos via OpenAI.")
    parser.add_argument("csv", type=Path, help="Caminho do arquivo kpis_semanais.csv")
    parser.add_argument("--model", default="gpt-4o-mini", help="Modelo da OpenAI")
    args = parser.parse_args()

    # Validações de ambiente e existência de arquivo
    chave_api = os.getenv("OPENAI_API_KEY")
    if not chave_api:
        print("❌ ERRO: OPENAI_API_KEY não configurada no ambiente!")
        sys.exit(1)

    if not args.csv.is_file():
        print(f"❌ ERRO: Arquivo não encontrado: {args.csv}")
        sys.exit(1)

    print(f"📂 Carregando dados de: {args.csv.name}")
    
    # Agrupando dados por semana em memória usando defaultdict(list)
    dados_por_semana = defaultdict(list)
    with args.csv.open("r", encoding="utf-8-sig", newline="") as f:
        leitor = csv.DictReader(f, delimiter=";" if ";" in f.read(4096) else ",")
        f.seek(0)
        # Re-ler após checar delimitador
        leitor = csv.DictReader(f, delimiter=";" if ";" in f.read(2048) else ",")
        f.seek(0)
        next(leitor) # pular cabeçalho na re-leitura cega se sniffer falhar, melhor ler normal:
        
    with args.csv.open("r", encoding="utf-8-sig", newline="") as f:
        # Detecta delimitador dinamicamente de forma segura
        amostra = f.read(2048)
        delim = ";" if ";" in amostra else ","
        f.seek(0)
        leitor = csv.DictReader(f, delimiter=delim)
        for linha in leitor:
            dados_por_semana[linha["semana"]].append(linha)

    print(f"📊 Total de semanas únicas detectadas: {len(dados_por_semana)}")
    
    # Caminho do arquivo de saída de dimensão (insights_semanais.csv)
    caminho_saida = args.csv.parent / "insights_semanais.csv"
    
    # Carregar insights existentes se o arquivo já existir para evitar chamadas redundantes (Idempotência)
    insights_existentes = {}
    if caminho_saida.exists():
        with caminho_saida.open("r", encoding="utf-8-sig", newline="") as f:
            leitor_out = csv.DictReader(f)
            if leitor_out.fieldnames and "semana" in leitor_out.fieldnames:
                for row in leitor_out:
                    insights_existentes[row["semana"]] = row["insight"]

    linhas_saida = []
    
    print("\n🚀 Iniciando consolidação e análise por inteligência artificial...")
    
    # Iteração sobre cada semana agrupada
    for num, (semana, linhas) in enumerate(sorted(dados_por_semana.items()), start=1):
        print(f"🔄 Processando {num}/{len(dados_por_semana)}: Semana {semana}...", end="", flush=True)
        
        # Verifica se a semana já foi analisada em execuções anteriores
        if semana in insights_existentes and insights_existentes[semana].strip():
            print(" ⏭️ (Já possuía insight gravado. Pulando...)")
            linhas_saida.append({"semana": semana, "insight": insights_existentes[semana]})
            continue

        # Realiza a agregação matemática dos KPIs da semana (soma de receitas e metas)
        rec_total = sum(converter_float(l["receita_liquida"]) for l in linhas)
        meta_total = sum(converter_float(l["meta_receita"]) for l in linhas)
        
        # Médias operacionais simples para o briefing descritivo
        tkt_medio = sum(converter_float(l["ticket_medio"]) for l in linhas) / len(linhas)
        taxa_devo = sum(converter_float(l["taxa_devolucao_pct"]) for l in linhas) / len(linhas)
        nps_medio = sum(converter_float(l["nps"]) for l in linhas) / len(linhas)
        
        # Monta o detalhamento textual regional agrupando cada linha do bloco
        detalhe_regional = []
        for l in linhas:
            detalhe_regional.append(
                f"- Região {l['regiao']}: Receita {formatar_moeda(converter_float(l['receita_liquida']))} "
                f"(Var vs Semana Anterior: {l['var_semana_anterior_pct']}%)"
            )
        regioes_texto = "\n".join(detalhe_regional)

        # Monta o briefing textual perfeitamente estruturado para a OpenAI
        briefing = f"""Dados Consolidados da Semana {semana}:
- Receita Líquida Total Realizada: {formatar_moeda(rec_total)}
- Meta de Receita Estipulada: {formatar_moeda(meta_total)}
- Desempenho por Região:
{regioes_texto}
- Médias Operacionais da Semana:
  * Ticket Médio Geral: {formatar_moeda(tkt_medio)}
  * Taxa de Devolução Média: {taxa_devo:.2f}%
  * NPS Médio: {nps_medio:.0f} pontos"""

        try:
            # Solicita o insight executivo à API
            insight_gerado = requisitar_insight(briefing, chave_api, args.model)
            linhas_saida.append({"semana": semana, "insight": insight_gerado})
            print(" ✓ Insight criado!")
            time.sleep(0.5) # Pausa estratégica para controle de Rate Limit
        except Exception as e:
            print(f" ❌ Erro ao analisar: {e}")
            linhas_saida.append({"semana": semana, "insight": "Erro ao gerar análise automatizada para esta semana."})

    # Escrita Atômica do arquivo final de Dimensão para prevenir corrupção em caso de interrupção
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", delete=False, dir=caminho_saida.parent, suffix=".tmp") as tmp:
        escritor = csv.DictWriter(tmp, fieldnames=["semana", "insight"])
        escritor.writeheader()
        escritor.writerows(linhas_saida)
        nome_temp = tmp.name

    Path(nome_temp).replace(caminho_saida)
    print(f"\n✅ Concluído com sucesso! Tabela de dimensão salva em: {caminho_saida.name}\n")

# Ponto de entrada padrão do script Python
if __name__ == "__main__":
    main()