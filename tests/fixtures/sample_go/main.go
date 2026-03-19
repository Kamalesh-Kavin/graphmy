// Sample Go source file for testing the Go parser.
// Exercises: functions, methods on types, structs, interfaces, imports.

package main

import (
	"fmt"
	"strings"
)

// Animal is an interface that all animals must implement.
type Animal interface {
	Speak() string
	Describe() string
}

// Dog is a struct representing a dog.
type Dog struct {
	Name  string
	Age   int
	Breed string
}

// Speak makes the dog bark.
func (d *Dog) Speak() string {
	return fmt.Sprintf("%s says: Woof!", d.Name)
}

// Describe returns a human-readable description of the dog.
func (d *Dog) Describe() string {
	return fmt.Sprintf("%s (%s, age %d)", d.Name, d.Breed, d.Age)
}

// NewDog creates a new Dog with the given attributes.
func NewDog(name, breed string, age int) *Dog {
	return &Dog{Name: name, Breed: breed, Age: age}
}

// Greet returns a greeting message.
func Greet(name string) string {
	return fmt.Sprintf("Hello, %s!", name)
}

// processNames takes a slice of names and returns them uppercased.
func processNames(names []string) []string {
	result := make([]string, len(names))
	for i, name := range names {
		result[i] = strings.ToUpper(name)
	}
	return result
}

func main() {
	dog := NewDog("Rex", "Labrador", 3)
	fmt.Println(dog.Speak())
	fmt.Println(Greet("world"))
	names := processNames([]string{"alice", "bob"})
	fmt.Println(names)
}
