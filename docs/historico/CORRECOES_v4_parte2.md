# Mudanças da v4 (parte 2) — fotos reais + frente/verso

## 1. As 6 fotos que você mandou já vêm prontas, sem precisar subir nada

Copiei as fotos (Regular, Baby Look e Oversize, frente e costas) pra
dentro do projeto, em `media/mockups/seed/`. Quando o app inicia,
ele cadastra essas 6 fotos automaticamente na cor "Preto" — você não
precisa passar pelo Admin pra ter isso funcionando.

Isso roda toda vez que o app inicia, mas é seguro: se a foto pra
aquela combinação (modelo + cor + lado) já existe, ele não duplica
nem sobrescreve. Se um dia você subir uma foto diferente pra Regular
Preto Frente pelo Admin, ela substitui a seedada — o sistema não fica
"brigando" com o que você cadastrar manualmente depois.

**Como calculei onde a arte entra em cada foto:** não chutei — usei
processamento de imagem (Pillow) pra detectar de verdade onde fica o
torso de cada camiseta em cada foto, e a partir disso posicionei a
área de impressão. Gerei um contact sheet conferindo visualmente as
6 fotos com o retângulo desenhado por cima antes de fechar os
números — ajustei a da Oversize Frente, que estava encostando demais
na gola. Se alguma ainda não estiver perfeita no seu uso real, dá pra
ajustar os números direto no Admin (a mesma tela que já existia).

## 2. Modelos atualizados: Regular, Oversize e Baby Look

Troquei "Boxy" por "Baby Look" no `config.json`, já que foi isso que
você mandou fotos. Se quiser voltar a ter um 4º modelo (Boxy ou
outro) no futuro, é só adicionar no `config.json` e subir as fotos
correspondentes pelo Admin — o sistema já foi feito pra aceitar
quantos modelos você cadastrar, os 3 atuais não são um limite fixo.

## 3. Seletor de Modelo virou 3 abas

Em vez do menu suspenso, agora tem 3 botões (Regular / Oversize /
Baby Look) — mais rápido de comparar e mais parecido com o resto do
site.

## 4. Frente e Verso agora são vistas separadas de verdade

Adicionei duas abas dentro do editor: **Frente** e **Verso**. Cada
uma tem:
- sua própria foto de mockup (ou desenho, se não houver foto pra
  aquele lado);
- sua própria área de impressão, na posição certa pra aquele lado;
- suas próprias artes — o que você coloca na Frente não aparece no
  Verso e vice-versa, são conjuntos de elementos independentes.

Ao finalizar o pedido, o sistema gera **um arquivo de produção pra
cada lado que tiver alguma arte** (só frente, só verso, ou os dois).
O nome dos arquivos na pasta do pedido:

```
ARTE_PRODUCAO.png          ← frente
ARTE_PRODUCAO_COSTAS.png   ← verso (só existe se você desenhou algo lá)
```

Não precisei mexer na lógica que copia pra pasta de produção depois
do pagamento — ela já pega qualquer arquivo que comece com
`ARTE_PRODUCAO`, então os dois lados chegam certinho pro operador sem
mudança nenhuma ali.

## 5. Cadastro de mockup no Admin ganhou o campo "Lado"

Faltava um jeito de cadastrar a foto do *verso* pelo Admin — o
formulário só tinha Modelo e Cor. Adicionei o seletor Frente/Verso,
então agora dá pra completar as fotos que faltam (outras cores, por
exemplo) sem precisar mexer em código.
