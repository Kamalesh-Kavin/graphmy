// Sample JavaScript source file for testing the JS/TS parser.
// Exercises: functions, classes, methods, ESM imports, async functions.

import { readFile } from 'fs/promises';

/**
 * Returns a greeting message.
 * @param {string} name - The name to greet.
 * @returns {string}
 */
function greet(name) {
  return `Hello, ${name}!`;
}

/**
 * Fetch data from a URL asynchronously.
 * @param {string} url
 * @param {number} timeout
 */
async function fetchData(url, timeout = 30) {
  const message = greet('world');
  return { url, message };
}

/**
 * Base Animal class.
 */
class Animal {
  constructor(name, age) {
    this.name = name;
    this.age = age;
  }

  speak() {
    throw new Error('Not implemented');
  }

  describe() {
    return `${this.name} (age ${this.age})`;
  }
}

/**
 * Dog extends Animal.
 */
class Dog extends Animal {
  constructor(name, age, breed) {
    super(name, age);
    this.breed = breed;
  }

  speak() {
    return `${this.name} says: Woof!`;
  }

  fetch(item) {
    const greeting = greet(item);
    return `${this.name} fetched ${item}. ${greeting}`;
  }
}

export { greet, fetchData, Animal, Dog };
