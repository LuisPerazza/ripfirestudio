# Mudanças da v4

## 0. Bug crítico corrigido (v4 → não tinha sido testada ainda)
Ao trocar para "Somente DTF", nem a camiseta nem a chapa de produção
apareciam — a tela do editor ficava vazia. Causa: o JavaScript tentava
"mostrar" os painéis limpando o `style.display` deles
(`el.style.display=''`), mas o CSS tinha uma regra
`.gang-only{display:none}` que continuava valendo assim que o estilo
inline era limpo (limpar não é o mesmo que sobrescrever). Corrigido
usando valores explícitos (`'block'`/`'none'`) em vez de string vazia.
Também corrigi uma mensagem de status que aparecia no painel errado
(longe do botão "Adicionar ao arquivo"), o que também dava a
impressão de que nada acontecia ao clicar.

## 1. Camiseta com visual mais 3D (Regular / Oversized / Boxy)

O preview parado (um retângulo colorido) virou um mockup em SVG com:
- silhueta diferente para cada modelo (Regular mais justa, Oversized com
  ombro caído e mais larga, Boxy reta e quadrada);
- sombreamento em gradiente (mais escuro nas bordas, mais claro no
  centro) pra dar sensação de volume;
- sombra sob a peça.

Isso é 100% código (SVG + gradiente), sem depender de foto nenhuma. Se
mais pra frente você quiser trocar por fotos reais dos produtos (o que
fica ainda mais realista), a troca é isolada: é só reescrever a função
`updateShirtSvg()` em `static/app.js` pra desenhar uma `<img>` da foto
por trás da área de impressão, no lugar do SVG. O resto do editor
(mover/girar/redimensionar a arte) continua igual.

## 1b. Mockup da camiseta refeito de novo (silhueta + sombreamento)
A primeira versão do SVG tinha uma gola com um "bico" esquisito (a
linha da gola era feita de segmentos retos em zigue-zague, não uma
curva) e mangas com cara de trapézio reto. Reconstruí do zero:
- a silhueta agora é gerada por parâmetros (largura de ombro, queda de
  manga, largura de barra etc.) com cantos arredondados de verdade,
  em vez de pontos fixos digitados à mão;
- a gola agora é uma curva suave (arco), não mais um zigue-zague;
- o sombreamento ganhou mais camadas: gradiente lateral (mais escuro
  nas bordas), um brilho no peito, uma sombra mais forte na base, e
  uma textura sutil de "ruído" simulando a trama do tecido.

Continua sendo 100% vetor (sem foto) — não chega no nível de realismo
de uma foto de produto de verdade (como a imagem que você mandou),
mas fica bem mais parecido com um mockup de e-commerce do que o
retângulo/desenho simples de antes.

## 1c. Agora dá pra usar foto de verdade em vez do desenho
Adicionei uma seção nova no Admin: **"Foto de mockup da camiseta"**.

Como usar:
1. No Admin (com a chave), escolha o Modelo e a Cor.
2. Suba uma foto da camiseta lisa (sem estampa), de frente, bem
   centralizada — igual você fotografa pros produtos do site.
3. Ajuste os campos "Área X/Y/Largura/Altura (%)" olhando o preview
   com o retângulo tracejado que aparece embaixo da foto — isso define
   onde a arte do cliente vai ser desenhada em cima da foto (em
   porcentagem da imagem, não em cm, então funciona em qualquer
   tamanho de foto).
4. Salvar.

A partir daí, sempre que o cliente escolher esse Modelo + Cor no
editor, a foto aparece no lugar do desenho — automaticamente, sem
precisar mexer em mais nada. Se você não cadastrar uma foto pra
alguma combinação específica (ex: só cadastrou Regular Preto, mas o
cliente escolhe Oversized Branco), o editor cai de volta pro desenho
SVG pra essa combinação, então não fica quebrado enquanto você não
tiver todas as fotos.

Dá pra apagar um mockup cadastrado (volta a usar o desenho) na mesma
lista, embaixo do formulário.

**Uma foto por combinação modelo+cor.** Subir de novo a mesma
combinação substitui a foto anterior.

**Importante — isso não recorta nem gera sombra automática:** a arte
do cliente é desenhada em cima da foto como uma camada plana, dentro
do retângulo que você definir. Ainda não simula a arte "vestindo" o
tecido (seguindo dobras, textura, sombra do próprio tecido por cima).
Pra chegar no nível exato da imagem de referência que você mandou
(onde a estampa parece impressa na trama do tecido), o próximo passo
seria aplicar um blend de multiplicação entre a arte e a textura da
foto — posso implementar isso depois que você tiver testado essa
versão com fotos reais.

## 2. "Somente DTF" virou um construtor de chapa de produção

Antes, a aba "Somente DTF" mostrava a camiseta e um campo de
quantidade avulso — não fazia muito sentido, já que nessa modalidade
o cliente nem recebe camiseta.

Agora, ao escolher "Somente DTF", o editor troca completamente para
uma tela de montagem de chapa (igual ao print que você mandou):

- painel "Adicionar imagem": upload, largura (a altura calcula sozinha
  pela proporção da imagem), quantidade, botão "Adicionar ao arquivo";
- lista de imagens adicionadas, com miniatura, tamanho e quantidade
  editável (mudar a quantidade já reorganiza a chapa);
- área de impressão com régua em cm nas duas bordas, largura fixa em
  58,5cm (o rolo — configurável em `config.json` →
  `dtf_sheet_width_cm`) e altura que cresce sozinha conforme você
  adiciona imagens;
- **reorganização automática**: toda vez que você adiciona uma
  imagem, muda a quantidade, ou clica em "Automático"/"Reorganizar",
  o sistema reempacota tudo sozinho, tentando girar 90° as peças que
  encaixam melhor giradas (fica marcado em verde na legenda);
- dá pra arrastar qualquer peça manualmente depois — se duas peças
  ficarem sobrepostas, a borda fica vermelha (colisão).

### Como funciona o encaixe automático
Uso um algoritmo de "prateleiras": ordena as peças da mais alta pra
mais baixa e vai enchendo linhas dentro da largura do rolo, testando
girar a peça 90° quando isso ajuda a caber. Não é o encaixe
matematicamente mais compacto possível (isso é um problema bem mais
complexo, chamado bin packing), mas é o mesmo tipo de abordagem usada
na prática por ferramentas de gang sheet — dá um resultado bom e
previsível sem deixar a tela lenta.

### Preço
A modalidade "Somente DTF" agora calcula o preço pela metragem total
da chapa gerada, com faixas configuráveis em `config.json` →
`pricing.dtf_gang_sheet_tiers` (hoje: até 1m R$49,90/m, até 3m
R$44,90/m, acima disso R$39,90/m) — no mesmo espírito da precificação
por metro que você tinha descrito lá no Ripfire Studio original. As
outras duas modalidades (DTF + camiseta, Camiseta pronta) continuam
com o preço por unidade de antes, sem mudança.

### O que exportei pro pedido
O arquivo final que vai para produção agora é a chapa inteira, já
renderizada no DPI configurado (`config.json` → `dpi`), do mesmo jeito
que antes exportava o design único — o backend não precisou de
nenhuma mudança pra isso.

## Não mexi (mas fica registrado)
- O encaixe automático hoje não tenta *reduzir* a rotação de peças já
  giradas manualmente por você — cada clique em "Automático" ou
  "Reorganizar" refaz tudo do zero. Se isso incomodar no uso real, dá
  pra guardar o que foi arrastado manualmente e só reorganizar o
  resto — mas isso é bem mais código, deixei simples por enquanto.
- Não implementei mockup fotográfico ainda (ver item 1) — segue como
  próximo passo natural quando você tiver as fotos.
