# AI Business Intelligence: Análise de Sentimentos e Geração de Insights com IA

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python)
![OpenAI](https://img.shields.io/badge/OpenAI-gpt--4o--mini-green?style=flat-square&logo=openai)
![Power BI](https://img.shields.io/badge/Power_BI-Dashboard-yellow?style=flat-square&logo=powerbi)

Trabalho acadêmico avaliativo desenvolvido para a pós-graduação **Data Analytics e Inteligência Artificial Aplicada a Negócios** - **FNAT** (Fundação de Negócios, Analytics e Tecnologia)

---

## 🎯 Contexto do Problema

A rede varejista **Valore** (48 lojas, ~R$ 310M/ano) enfrenta dois gargalos operacionais críticos:

1. **Volume Não Estruturado**: 1.200+ avaliações de clientes mensais (App, Site, Google) sem capacidade de análise manual em tempo hábil
2. **Falta de Contexto em KPIs**: Variações de receita, NPS ou ticket médio exigem semanas de análise manual para identificar causa raiz

Este projeto implementa um sistema de processamento em lote com orquestração via scripts Python e integração com a API OpenAI para classificação de sentimentos e geração de insights narrativos, consumidos por dashboard Power BI.

---

## 🏗️ Arquitetura da Solução

Dois scripts Python executados manualmente via arquivo batch (`processar_dados.bat`):

### 1. `classificar_sentimentos.py`
- **Entrada**: avaliacoes_clientes.csv (1.200 itens)
- **Modelo**: gpt-4o-mini (OpenAI)
- **Saída**: Colunas "sentimento" (positivo/neutro/negativo) e "tema_principal" (6 categorias)
- **Otimização**: Processa em lotes de 10, cache nativo (pula registros já classificados), retry automático com exponential backoff

### 2. `gerar_insights_semanais.py`
- **Entrada**: kpis_semanais.csv (52 semanas)
- **Modelo**: gpt-4o-mini (OpenAI)
- **Contexto**: Receita vs meta, variação regional, ticket médio, taxa de devolução, NPS
- **Saída**: insights_semanais.csv (narrativas de 2-3 linhas/semana)
- **Otimização**: Cache por semana, evita reprocessamento

### 3. Power BI (Consumidor)
- Lê avaliacoes_clientes.csv com classificações
- Lê insights_semanais.csv com narrativas
- Monta dashboard integrado com filtros cruzados
- **Sem chamadas à API**: apenas visualiza dados já enriquecidos

[Visualizaçao Completa do Dashboard](https://app.powerbi.com/view?r=eyJrIjoiMjNiMjcxZDgtM2JmYy00MDNjLWI1MjgtYWJlMmQxMTk0MzNlIiwidCI6ImFlMTViNmUwLWVmZmMtNDI5NS1iMjg5LWY3ZWYyY2JiOWZhNiJ9&pageName=2887a3a4b000adec1983)
---

## 📋 Fluxo de Execução

```
1. Definir OPENAI_API_KEY (variável de ambiente)
   ↓
2. Executar processar_dados.bat
   ├─ python gerar_insights_semanais.py kpis_semanais.csv
   └─ python classificar_sentimentos.py avaliacoes_clientes.csv
   ↓
3. Scripts fazem requisições HTTP síncronas à API OpenAI
   ↓
4. Batch finaliza com confirmação
   ↓
5. Power BI refresha e lê CSVs atualizados
   ↓
6. Dashboard exibe análises consolidadas
```

---

## 💰 Custos e ROI

### Primeira Atualização Completa
- **Avaliações** (1.200 itens): R$ 0,27
- **Insights KPI** (52 semanas): R$ 0,03
- **Total**: R$ 0,30

### Estado Estacionário (Ano 2+)
- **Avaliações com cache** (75 novos/mês): R$ 0,20/ano
- **Insights com cache** (50 semanas novas/ano): R$ 0,03/ano
- **Total**: R$ 0,23/ano

### ROI Comparativo
- **Economia anual**: ~300 horas de análise manual (~R$ 30.000)
- **Investimento**: R$ 0,30 primeira vez + R$ 0,23/ano
- **Retorno**: 100.000:1

---



## 🚀 Como Utilizar

### Pré-requisitos

- Python 3.10+
- Chave de API OpenAI

### Configuração da Chave de API

**Linux/macOS:**
```bash
export OPENAI_API_KEY="sua-chave-api-aqui"
```

**Windows (CMD):**
```cmd
set OPENAI_API_KEY="sua-chave-api-aqui"
```

**Windows (PowerShell):**
```powershell
$env:OPENAI_API_KEY="sua-chave-api-aqui"
```

### Execução

1. Abra a pasta do projeto
2. Execute: `processar_dados.bat`
3. Aguarde a conclusão (mensagem de confirmação)
4. Refreshe o dashboard Power BI

---

## 📊 Principais Características

| Aspecto | Detalhe |
|--------|--------|
| **Modelos** | gpt-4o-mini (ambos os scripts) |
| **Escalabilidade** | 1.200+ avaliações + 52 semanas KPI |
| **Cache** | Incremental, reduz chamadas API em ~95% |
| **Integridade** | Gravação atômica, retry exponencial |
| **Conformidade** | LGPD-ready, sem PII transmitido |
| **Custo** | R$ 0,30 inicial + R$ 0,23/ano |

---

## 📄 Estrutura de Arquivos

```
projeto/
├── processar_dados.bat              # Orquestrador de scripts
├── classificar_sentimentos.py        # Script 1: Análise de sentimento
├── gerar_insights_semanais.py        # Script 2: Geração de insights
├── avaliacoes_clientes.csv           # Dados de entrada (+ colunas de output)
├── kpis_semanais.csv                 # Dados de entrada
├── insights_semanais.csv             # Saída (gerada automaticamente)
└── README.md                         # Este arquivo
```

---

## ⚠️ Notas Importantes

- **Variável de Ambiente**: A chave OPENAI_API_KEY deve estar definida antes de executar o batch
- **Conectividade**: Scripts requerem conexão ativa com internet (chamadas síncronas à API)
- **CSVs**: Manter delimitadores consistentes (`;` ou `,`) dentro de cada arquivo
- **Power BI**: Não realiza chamadas à API, apenas lê CSVs processados

---

## 📝 Licença e Referência Acadêmica

Trabalho desenvolvido para fins acadêmicos.
