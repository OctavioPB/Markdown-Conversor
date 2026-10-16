# DRAFT — requiere revisión humana (borrador de Pandoc adaptado al dialecto)

# Python (programming language)

## Code examples

["Hello, World!" program](https://en.wikipedia.org/wiki/%22Hello,_World!%22_program):

```python
print('Hello, World!')
```

Program to calculate the [factorial](https://en.wikipedia.org/wiki/Factorial) of a non-negative integer:

```python
text = input('Type a number, and its factorial will be printed: ')

if text.isdigit():
    n = int(text)
else:
    raise TypeError('You must input a number')

if n < 0:
    raise ValueError('You must enter a non-negative integer')

factorial = 1
for i in range(2, n + 1):
    factorial *= i

print(factorial)
```
