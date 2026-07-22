"""
Classificador de Sentimentos e Temas em CSV usando API OpenAI (Processamento em Lote)

Este script lê um arquivo CSV contendo avaliações de clientes de e-commerce e 
preenche automaticamente duas colunas de análise:
  1. Sentimento (positivo, neutro ou negativo)
  2. Tema Principal (entrega, qualidade do produto, atendimento, etc.)

DIFERENCIAIS TÉCNICOS:
  - Processamento em Lote (Batching): Agrupa registros para reduzir requisições à API.
  - Saída Estruturada (Structured Outputs / JSON Schema): Garante respostas em JSON estrito.
  - Ancoragem por ID: Associa cada resultado ao 'id_avaliacao' original, eliminando
    riscos de desalinhamento de dados na gravação do CSV.
  - Persistência e Backup: Atualizações atômicas e suporte a retentativas com tratamento de erros.

REQUISITOS:
  - Variável de ambiente OPENAI_API_KEY configurada.
  - Arquivo CSV contendo as colunas: 'id_avaliacao', 'texto_avaliacao' e 'nota'.

EXEMPLO DE USO:
  python classificar_sentimentos.py "avaliacoes_clientes"
"""

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ============================================================================
# SEÇÃO DE CONFIGURAÇÃO (CONSTANTES CONFIGURÁVEIS)
# ============================================================================

# Quantidade de registros enviados conjuntamente por requisição à API
TAMANHO_LOTE = 10

# Número máximo de tentativas de reenvio em caso de falha temporária na API
MAX_TENTATIVAS = 5

# Tempo limite máximo (em segundos) de espera por uma resposta da rede
TIMEOUT = 60

# Nomes padronizados das colunas de entrada e saída no CSV
COLUNA_TEXTO = "texto_avaliacao"
COLUNA_SENTIMENTO = "sentimento"
COLUNA_TEMA = "tema_principal"
COLUNA_NOTA = "nota"
COLUNA_ID = "id_avaliacao"

# Diretrizes do sistema (System Prompt) orientando o modelo de linguagem sobre
# as regras de decisão, escopo e restrições de classificação.
INSTRUCOES = """
Você é um especialista em análise de sentimentos de avaliações de clientes de um e-commerce de moda.

Cada item recebido para análise seguirá rigorosamente a seguinte estrutura:
[REGISTRO: [ID_DA_AVALIACAO]]
- Texto: "[Comentário do cliente]"
- NOTA ATRIBUÍDA: [Nota de 1 a 5] de 5

Sua tarefa é classificar cada avaliação recebida em exatamente três campos no seu retorno JSON:
1. id_avaliacao (retorne exatamente o ID correspondente recebido)
2. sentimento
3. tema_principal

Você deve processar cada um dos itens enviados individualmente e retornar uma lista contendo todos eles.

========================
CLASSIFICAÇÃO DO SENTIMENTO
========================

Você deve seguir regras matemáticas estritas baseadas na "NOTA ATRIBUÍDA" para definir o sentimento, usando o "Texto" apenas para confirmar se não há uma contradição extrema.

Regras de Decisão baseadas na NOTA ATRIBUÍDA:
- NOTA 1 ou 2: Classifique OBRIGATORIAMENTE como "negativo". (Exceção única: se o comentário for um elogio extremo e a nota baixa tiver sido claramente um erro de digitação do cliente ao clicar na estrela).
- NOTA 3: Classifique OBRIGATORIAMENTE como "neutro". (Exceção única: se o comentário contiver palavras de forte insatisfação/frustração, mude para "negativo").
- NOTA 4 ou 5: Classifique OBRIGATORIAMENTE como "positivo". (Exceção única: se o comentário for uma reclamação grave de produto quebrado ou entrega não realizada, mude para "negativo").

A nota é o seu balizador primário de decisão. Não ignore a nota fornecida sob nenhuma circunstância.

========================
CLASSIFICAÇÃO DO TEMA
========================

Escolha APENAS UM tema principal com base no "Texto" da avaliação.

Utilize as seguintes definições:

entrega
Problemas ou elogios relacionados ao prazo, transporte, frete, entrega ou recebimento.

qualidade do produto
Material, acabamento, durabilidade, defeitos, aparência, costura, resistência ou qualidade geral.

atendimento
Atendimento da loja, vendedores, suporte, SAC ou relacionamento com o cliente.

tamanho e caimento
Numeração, medidas, modelagem, ajuste ao corpo, caimento ou tamanho.

troca e devolução
Trocas, devoluções, reembolsos ou garantia.

preço
Preço, promoções, discounts ou custo-benefício.

Caso mais de um tema seja citado, escolha apenas aquele que representa o principal motivo da avaliação.

========================
REGRAS IMPORTANTES
========================

- Nunca invente informações.
- Nunca explique a resposta.
- Nunca escreva texto adicional.
- Responda exclusivamente no JSON definido pelo schema.
- Retorne exatamente a mesma quantidade de objetos recebidos, mapeados com seus respectivos IDs originais.
"""

# Definição formal do esquema JSON (JSON Schema) para a funcionalidade Structured Outputs.
# Força a API a responder estritamente dentro da estrutura e tipos de dados definidos.
JSON_SCHEMA = {
    "name": "classificacao_sentimentos_e_temas",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "analises": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id_avaliacao": {
                            "type": "string",
                            "description": "O ID original correspondente recebido na entrada."
                        },
                        "sentimento": {
                            "type": "string",
                            "enum": ["positivo", "neutro", "negativo"]
                        },
                        "tema_principal": {
                            "type": "string",
                            "enum": [
                                "entrega",
                                "qualidade do produto",
                                "atendimento",
                                "tamanho e caimento",
                                "troca e devolução",
                                "preço"
                            ]
                        }
                    },
                    "required": ["id_avaliacao", "sentimento", "tema_principal"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["analises"],
        "additionalProperties": False
    }
}

# ============================================================================
# FUNÇÕES UTILITÁRIAS DE TRATAMENTO DE DADOS E LEITURA
# ============================================================================

def esta_vazio(valor):
    """
    Verifica se um campo do CSV está ausente, nulo ou preenchido apenas com espaços.
    
    Retorna:
        bool: True se o valor for considerado vazio, False caso contrário.
    """
    return valor is None or not str(valor).strip()


def detectar_dialeto_csv(caminho_arquivo):
    """
    Analisa os primeiros bytes do arquivo para identificar automaticamente
    o formato e o delimitador correto do CSV (ex: vírgula, ponto e vírgula, tabulação).
    
    Prioriza o uso de ponto e vírgula (;) comum em padrões em português do Excel.
    
    Retorna:
        csv.Dialect: Objeto de dialect do módulo csv.
    """
    with caminho_arquivo.open("r", encoding="utf-8-sig", newline="") as arquivo:
        amostra = arquivo.read(8192)
    try:
        if ";" in amostra:
            dialect = csv.excel
            dialect.delimiter = ";"
            return dialect
        return csv.Sniffer().sniff(amostra, delimiters=";,\t|")
    except csv.Error:
        return csv.excel


def validar_resposta_json(dados_json, tamanho_esperado, ids_esperados):
    """
    Inspeciona e valida a estrutura retornada pela IA.
    
    Garante que a resposta contenha os campos obrigatórios (id_avaliacao, sentimento,
    tema_principal) e aplica valores padrão caso algum registro apresente inconsistência.
    
    Retorna:
        list[dict] ou None: Lista de dicionários sanitizados ou None em caso de falha estrutural.
    """
    try:
        # Normaliza a extração da lista de análises do payload retornado
        if isinstance(dados_json, list):
            lista_analises = dados_json
        elif isinstance(dados_json, dict):
            lista_analises = (
                    dados_json.get("analises")
                    or dados_json.get("sentimentos")
                    or list(dados_json.values())[0]
            )
        else:
            return None

        if not isinstance(lista_analises, list):
            return None

        # Conjuntos de validação para checagem de domínio (valores permitidos)
        valores_sentimento = {"positivo", "neutro", "negativo"}
        valores_tema = {
            "entrega", "qualidade do produto", "atendimento",
            "tamanho e caimento", "troca e devolução", "preço"
        }

        resultados_extraidos = []

        # Itera sobre cada registro parseado validando os tipos e conteúdos
        for item in lista_analises:
            id_val = "DESCONHECIDO"
            sent_val = "neutro"
            tema_val = "qualidade do produto"

            if isinstance(item, dict):
                id_val = item.get("id_avaliacao") or item.get("id") or "DESCONHECIDO"
                id_val = str(id_val).strip()

                s = item.get("sentimento")
                if s and str(s).strip().lower() in valores_sentimento:
                    sent_val = str(s).strip().lower()

                t = item.get("tema_principal")
                if t and str(t).strip().lower() in valores_tema:
                    tema_val = str(t).strip().lower()

            resultados_extraidos.append({
                "id_avaliacao": id_val,
                "sentimento": sent_val,
                "tema_principal": tema_val
            })

        return resultados_extraidos
    except Exception:
        return None


def extrair_json_da_resposta(resposta_json):
    """
    Navega pelo payload retornado da API Responses da OpenAI e localiza o bloco
    de texto contendo o JSON estruturado.
    
    Suporta respostas formatadas com marcadores de código Markdown (```json ... ```) 
    ou objetos JSON puros.
    
    Retorna:
        dict: O conteúdo do JSON decodificado como dicionário Python.
    """
    try:
        for item in resposta_json.get("output", []):
            if item.get("type") != "message":
                continue
            for conteudo in item.get("content", []):
                if conteudo.get("type") == "output_text":
                    texto = conteudo.get("text", "").strip()
                    if texto.startswith("```json"):
                        texto = texto.split("```json", 1)[1].rsplit("```", 1)[0].strip()
                    elif texto.startswith("```"):
                        texto = texto.split("```", 1)[1].rsplit("```", 1)[0].strip()
                    return json.loads(texto)
        raise ValueError("Nenhum conteúdo de texto encontrado na resposta estruturada.")
    except Exception as e:
        # Mecanismo de busca alternativo para variação de estrutura da resposta
        try:
            for item in resposta_json.get("output", []):
                if "message" in item and "content" in item["message"]:
                    for c in item["message"]["content"]:
                        if c.get("type") == "text":
                            return json.loads(c["text"])
        except Exception:
            pass
        raise ValueError(f"Não foi possível extrair o JSON estruturado: {e}")


# ============================================================================
# COMUNICAÇÃO COM A API E GESTÃO DE ERROS
# ============================================================================

def lidar_com_erro_429(erro, tentativa):
    """
    Trata erros de limite de taxa (Rate Limit - HTTP 429).
    
    Aplica a pausa indicada pelo cabeçalho 'Retry-After' do servidor ou faz o uso
    da estratégia de Backoff Exponencial (2^tentativa segundos) para evitar bloqueios.
    """
    retry_after = erro.headers.get("Retry-After")
    if retry_after:
        try:
            espera = float(retry_after)
            print(f"   ⏳ Limite atingido (429). Aguardando {espera} segundos (Retry-After)...")
            time.sleep(espera)
            return
        except ValueError:
            pass

    espera_backoff = 2 ** tentativa
    print(f"   ⏳ Limite atingido (429). Aguardando {espera_backoff} segundos (Backoff Exponencial)...")
    time.sleep(espera_backoff)


def classificar_lote(lista_comentarios, lista_notas, lista_ids, chave_api, modelo):
    """
    Prepara o payload, estabelece conexão HTTPS com o endpoint da API OpenAI e 
    submete um lote de registros para classificação em chamada única.
    
    Retorna:
        list[dict]: Lista contendo as análises validadas de cada registro no lote.
    """
    # Define a margem de tokens de saída necessária para acomodar a estrutura do JSON
    tokens_por_comentario = 150
    max_tokens = len(lista_comentarios) * tokens_por_comentario

    # Monta a estrutura da mensagem concatenando ID, comentário e nota atribuída
    input_estruturado = [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": f"[REGISTRO: {id_av}]\n- Texto: \"{comentario}\"\n- NOTA ATRIBUÍDA: {nota} de 5"
                }
            ]
        }
        for id_av, comentario, nota in zip(lista_ids, lista_comentarios, lista_notas)
    ]

    # Prepara o corpo final da requisição HTTP com parâmetro JSON Schema
    corpo_requisicao = json.dumps({
        "model": modelo,
        "instructions": INSTRUCOES,
        "input": input_estruturado,
        "max_output_tokens": max_tokens,
        "temperature": 0,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "classificacao_sentimentos_e_temas",
                "strict": True,
                "schema": JSON_SCHEMA["schema"]
            }
        }
    }).encode("utf-8")

    requisicao = Request(
        "https://api.openai.com/v1/responses",
        data=corpo_requisicao,
        headers={
            "Authorization": f"Bearer {chave_api}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    # Loop de tentativas com política de re-execução em caso de falhas temporárias de rede
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            with urlopen(requisicao, timeout=TIMEOUT) as resposta_http:
                resposta_corpo = resposta_http.read().decode("utf-8")
                dados_resposta = json.loads(resposta_corpo)

                json_interno = extrair_json_da_resposta(dados_resposta)
                analises = validar_resposta_json(json_interno, len(lista_comentarios), lista_ids)

                if analises is None:
                    raise ValueError("Falha na validação do JSON estruturado retornado.")

                return analises

        except HTTPError as erro:
            if erro.code == 429 and tentativa < MAX_TENTATIVAS:
                lidar_com_erro_429(erro, tentativa)
                continue

            detalhe = erro.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Erro HTTP {erro.code}: {detalhe}") from erro

        except (URLError, json.JSONDecodeError, ValueError) as erro:
            if tentativa == MAX_TENTATIVAS:
                raise RuntimeError(f"Falha persistente no lote após processamento: {erro}") from erro
            time.sleep(2 ** tentativa)

    raise RuntimeError("Não foi possível processar o lote devido a falhas sucessivas.")


# ============================================================================
# PERSISTÊNCIA ATÔMICA NO DISCO
# ============================================================================

def salvar_csv_atomico(caminho_arquivo, todas_linhas, nomes_colunas, dialeto):
    """
    Grava a base de dados em um arquivo temporário no disco e o renomeia para 
    substituir o original.
    
    Essa gravação atômica previne a perda ou corrupção do arquivo CSV original 
    caso o script seja interrompido abruptamente ou ocorra falha de energia.
    """
    with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            delete=False,
            dir=caminho_arquivo.parent,
            suffix=".tmp"
    ) as arquivo_temp:
        escritor = csv.DictWriter(
            arquivo_temp, 
            fieldnames=nomes_colunas, 
            dialect=dialeto,
            extrasaction="ignore"
        )
        escritor.writeheader()
        escritor.writerows(todas_linhas)
        nome_temp = arquivo_temp.name

    # Substituição atômica no sistema operacional
    Path(nome_temp).replace(caminho_arquivo)


# ============================================================================
# ORQUESTRAÇÃO E EXECUÇÃO PRINCIPAL
# ============================================================================

def main():
    # Interface de Linha de Comando (CLI) para parsing dos parâmetros informados
    parser = argparse.ArgumentParser(
        description="Preenche eficientemente sentimentos e temas vazios em um CSV usando processamento em lote ancorado em IDs."
    )
    parser.add_argument("csv", type=Path, help="Caminho do arquivo CSV")
    parser.add_argument("--model", default="gpt-4o-mini", help="Modelo da API OpenAI")
    parser.add_argument("--dry-run", action="store_true", help="Modo teste informativo")
    args = parser.parse_args()

    # Validação do ambiente e verificação dos parâmetros de entrada
    chave_api = os.getenv("OPENAI_API_KEY")
    if not chave_api:
        print("❌ ERRO: Variável de ambiente OPENAI_API_KEY não configurada!")
        sys.exit(1)

    if not args.csv.is_file():
        print(f"❌ ERRO: Arquivo não encontrado: {args.csv}")
        sys.exit(1)

    print(f"📂 Lendo arquivo: {args.csv.name}")
    dialeto = detectar_dialeto_csv(args.csv)

    # Leitura integral do CSV armazenando seu conteúdo em memória
    with args.csv.open("r", encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo, dialect=dialeto)
        nomes_colunas = leitor.fieldnames or []
        todas_linhas = list(leitor)

    # Localização de colunas obrigatórias ignorando diferenças entre caixa alta/baixa
    coluna_id_real = next((c for c in nomes_colunas if c.lower() == "id_avaliacao"), None)
    coluna_texto_real = next((c for c in nomes_colunas if c.lower() == "texto_avaliacao"), None)
    coluna_nota_real = next((c for c in nomes_colunas if c.lower() == "nota"), None)

    if not coluna_texto_real or not coluna_nota_real or not coluna_id_real:
        print(f"❌ ERRO: O CSV deve possuir as colunas 'id_avaliacao', 'texto_avaliacao' e 'nota' (independente de maiúsculas).")
        sys.exit(1)

    # Inclusão automática das colunas de resultado no cabeçalho caso ainda não existam
    if COLUNA_SENTIMENTO not in nomes_colunas:
        nomes_colunas.append(COLUNA_SENTIMENTO)
    if COLUNA_TEMA not in nomes_colunas:
        nomes_colunas.append(COLUNA_TEMA)

    # Identificação das linhas com pendência de classificação (Sentimento OU Tema em branco)
    linhas_pendentes = [
        (idx, linha) for idx, linha in enumerate(todas_linhas)
        if (esta_vazio(linha.get(COLUNA_SENTIMENTO)) or esta_vazio(linha.get(COLUNA_TEMA)))
           and not esta_vazio(linha.get(coluna_texto_real))
    ]

    total_total = len(todas_linhas)
    total_pendentes = len(linhas_pendentes)
    total_ja_classificados = total_total - total_pendentes

    print(f"📊 Análise:")
    print(f"   - Total de linhas: {total_total}")
    print(f"   - Já classificados: {total_ja_classificados}")
    print(f"   - Pendentes de classificação: {total_pendentes}")

    # Encerramento antecipado se executado em modo simulado (--dry-run)
    if args.dry_run:
        print("\n🧪 Modo dry-run ativado. Nenhuma chamada será realizada.")
        return

    if not linhas_pendentes:
        print("\n✅ Todos os registros já possuem sentimentos e temas definidos.")
        return

    # Criação de cópia de segurança (.bak) na primeira execução do arquivo
    caminho_backup = args.csv.with_suffix(args.csv.suffix + ".bak")
    if not caminho_backup.exists():
        shutil.copy2(args.csv, caminho_backup)
        print(f"🔒 Backup criado com sucesso: {caminho_backup.name}")

    # Divisão das linhas pendentes em pequenos blocos (lotes) configuráveis
    lotes = [linhas_pendentes[i:i + TAMANHO_LOTE] for i in range(0, total_pendentes, TAMANHO_LOTE)]
    total_lotes = len(lotes)

    print(f"\n🚀 Iniciando processamento de {total_pendentes} itens em {total_lotes} lote(s)...\n")

    contador_sucesso = total_ja_classificados

    # Loop de processamento e envio dos lotes à API
    for num_lote_seq, lote_atual in enumerate(lotes, start=1):
        indices_originais = [item[0] for item in lote_atual]

        textos_comentarios = []
        notas_comentarios = []
        ids_avaliacao = []

        # Extração e preparação das listas do lote corrente
        for item in lote_atual:
            linha = item[1]
            
            id_val = linha.get(coluna_id_real, "DESCONHECIDO")
            texto_val = linha.get(coluna_texto_real, "")
            nota_val = linha.get(coluna_nota_real, "3")

            ids_avaliacao.append(str(id_val).strip())
            textos_comentarios.append(texto_val)
            notas_comentarios.append(str(nota_val).strip())

        item_inicial = ((num_lote_seq - 1) * TAMANHO_LOTE) + 1
        item_final = item_inicial + len(lote_atual) - 1

        print(f"🔄 Lote {num_lote_seq}/{total_lotes} | Comentários: {item_inicial} até {item_final}")

        try:
            # Envio do lote para a API
            resultados_retornados = classificar_lote(
                textos_comentarios, 
                notas_comentarios, 
                ids_avaliacao, 
                chave_api, 
                args.model
            )

            # Mapeamento dos resultados por 'id_avaliacao' para garantir integridade relacional
            mapa_resultados = {res["id_avaliacao"]: res for res in resultados_retornados if "id_avaliacao" in res}

            # Atualização da estrutura de dados das linhas em memória
            for idx_original in indices_originais:
                linha_original = todas_linhas[idx_original]
                id_orig = str(linha_original.get(coluna_id_real, "")).strip()

                if id_orig and id_orig in mapa_resultados:
                    res = mapa_resultados[id_orig]
                    todas_linhas[idx_original][COLUNA_SENTIMENTO] = res["sentimento"]
                    todas_linhas[idx_original][COLUNA_TEMA] = res["tema_principal"]
                else:
                    # Fallback de segurança se o ID não for localizado no retorno
                    todas_linhas[idx_original][COLUNA_SENTIMENTO] = "neutro"
                    todas_linhas[idx_original][COLUNA_TEMA] = "qualidade do produto"

            # Gravação incremental do lote no arquivo CSV em disco
            salvar_csv_atomico(args.csv, todas_linhas, nomes_colunas, dialeto)
            contador_sucesso += len(lote_atual)

            print(f"   ✓ Lote concluído. Progresso geral: {contador_sucesso} / {total_total} classificados.")

        except Exception as erro:
            print(f"   ❌ Erro no lote {num_lote_seq}: {erro}. Pulando para o próximo lote...")
            continue

    print(f"\n✅ Fim do processamento! Alterações gravadas em: {args.csv.name}")

# Ponto de entrada padrão do script Python
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ Processamento cancelado manualmente.")
        sys.exit(1)
    except Exception as erro_geral:
        print(f"\n❌ Falha crítica inesperada: {erro_geral}")
        sys.exit(1)